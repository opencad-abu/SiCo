"""GUI startup owns only discovery waits and the remote frontend facade."""

from cadai.env_names import name as env_name

from ..core.contracts import BoundContext
from ..providers.config import resolve_provider_config
from .remote_frontend import RemoteFrontend
from .service_startup import ServiceStartup
from .startup_lifecycle import StartupLifecycle
from .startup_result import StartupFailure, StartupReady
from .window_lease import WindowAlreadyOpen, claim_window


class FrontendStartup(StartupLifecycle):
    """Remote attachment startup; no desktop backend or journal capabilities."""

    instructions = ""
    # 生产窗口模式一个工程只允许一个窗口；demo/connect 是脚本化联调路径，不占窗口锁。
    WINDOW_MODES = frozenset({"desktop-worker", "", "service-gui"})

    def _await(self, receipt, *, progress=None):
        while not receipt.done():
            self._check()
            if progress is not None:
                self.instructions = progress.instructions
            self._stop.wait(0.02)
        self._check()
        return receipt.result()

    def _configuration(self):
        self.phase = "config"
        config = resolve_provider_config(self.args.provider_config, launch_dir=self.args.launch_dir,
                                          environment=self.environment)
        key = config.get("api_key_env", env_name("API_KEY")) if config else None
        if key and not self.environment.get(key):
            self.phase = "credentials"
            self.configuration.set_result(key)
            self.environment[key] = self._receive(self._credential)
        else:
            self.configuration.set_result(None)
        self._check()
        return config

    def _prepare(self, resources):
        mode = getattr(self.args, "command", "desktop-worker")
        self._claim_window(resources, mode)
        registration = None
        if mode not in {"demo", "connect", "service-gui"}:
            registration = self._receive(self._registration, self.registration_timeout)
            if "environment" in registration:
                # Captured before the launcher sanitized the GUI process environment.
                self.environment = dict(registration["environment"])
            bound = BoundContext.from_record(registration["context"])
            bridge = registration["bridge"]
            if any(bridge[key] != getattr(bound, key) for key in ("instance_id", "generation")):
                raise ValueError("Desktop bridge and target identity mismatch")
            self.notice("ready", bridge_id=bridge["bridge_id"], router_id=bridge["router_id"],
                        instance_id=bound.instance_id, generation=bound.generation,
                        session_id=self.args.session, target_id=bound.target_id)
        config = None if mode == "service-gui" else self._configuration()
        if mode == "service-gui":
            self.configuration.set_result(None)
        self.phase = "discovery"
        startup = ServiceStartup(self.args.launch_dir)
        resources.callback(startup.close)
        descriptor = self._await(startup.ready)
        expected_service = getattr(self.args, "service_id", None)
        if expected_service and descriptor.service_id != expected_service:
            raise ValueError("原服务已结束，旧窗口唤醒已失效；请重新打开项目")
        self.phase = "attachment"
        api = RemoteFrontend(descriptor, self.args.launch_dir, self.args.session)
        if mode in {"demo", "connect"}:
            api._attach.timeout = max(api._attach.timeout, self.args.timeout + 30)
        resources.callback(api.close_desktop)
        if mode == "service-gui":
            operation, params = "attach", dict(session_id=self.args.session)
        elif mode in {"demo", "connect"}:
            operation, params = mode, dict(session_id=self.args.session, provider_config=config,
                environment=self.environment, timeout=self.args.timeout,
                context_file=getattr(self.args, "context_file", None))
        else:
            operation, params = "open", dict(session_id=self.args.session,
                provider_config=config, environment=self.environment,
                bridge=registration["bridge"], context=bound)
        created = []
        from ..transport.framing import ProtocolError

        try:
            frontend = self._await(api.open_initial(operation, params,
                observe_creation=created.append), progress=api._attach)
        except ProtocolError:
            raise
        except ValueError:
            if mode != "service-gui" or expected_service:
                raise
            frontend = self._await(api.recovery.open_history(self.args.session))
        expected_runtime = getattr(self.args, "runtime_id", None)
        if expected_runtime and frontend.session.runtime_id != expected_runtime:
            raise ValueError("原会话运行实例已结束，旧窗口唤醒已失效")
        if created == [True]:
            try:
                self._await(api.control.request(frontend.session, "acquire"))
            except ProtocolError:
                raise
            except ValueError:
                pass  # Reopening an occupied design remains an observer.
        return StartupReady(frontend, api.can_control(frontend.session), persistent=False,
                            initial_text=getattr(self.args, "message", "") or "",
                            attach_targets=mode not in {"demo", "connect", "service-gui"})

    def _claim_window(self, resources, mode):
        """Hold the project's window lock; a second live window is refused here."""

        if mode not in self.WINDOW_MODES:
            return
        try:
            lease = claim_window(self.args.launch_dir)
        except WindowAlreadyOpen as exc:
            raise StartupFailure("window", exc) from exc
        resources.callback(lease.close)
