; Saved-source live modeling bridge for one controller-owned Virtuoso session.
;
; The post-save trigger is intentionally a thin SKILL/UI boundary. It emits
; one bounded JSONL event through the existing aiIpcState['cid]; it does not
; call dbSave, traverse OA, netlist, simulate, invoke a model, or create a
; second controller/listener.

unless(and(boundp('aiLiveModelVersion)
             aiLiveModelVersion=="20260902.ai.live.v1"
             boundp('aiLiveModelState)
             isCallable('aiLiveModelPostSaveTrigger))

unless(boundp('aiLiveModelState)
  aiLiveModelState=makeTable("aiLiveModelState" nil))

procedure(aiLiveModelRevision()
  "20260902.ai.live.v1"
)

procedure(aiLiveModelPair(key value)
  list(key value)
)

procedure(aiLiveModelBoundText(value limit)
  if(and(stringp(value)
         strlen(value)>0
         strlen(value)<=limit
         rexMatchp("^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]*$" value))
    value
    nil)
)

procedure(aiLiveModelViewKind(viewType viewName)
  let((lower)
    lower=lowerCase(or(viewType viewName ""))
    cond(
      (member(lower list("schematic" "schematicsymbol")) "schematic")
      (member(lower list("symbol" "symbolic")) "symbol")
      (member(lower list("config" "configuration")) "config")
      (member(lower list("spec")) "spec")
      (member(lower list("layout" "masklayout")) "layout")
      (t nil)))
)

procedure(aiLiveModelIdentity(cv)
  prog((protected values identity lib cell view viewType kind)
    unless(cv return(nil))
    protected=errset(
      list(cv~>libName cv~>cellName cv~>viewName cv~>viewType) nil)
    unless(protected return(nil))
    values=car(protected)
    lib=aiLiveModelBoundText(car(values) 160)
    cell=aiLiveModelBoundText(cadr(values) 160)
    view=aiLiveModelBoundText(caddr(values) 160)
    ; Some Virtuoso cellview classes expose a nil viewType; the canonical
    ; view name is then the only authoritative type available to the callback.
    viewType=aiLiveModelBoundText(cadddr(values) 160)
    unless(viewType viewType=aiLiveModelBoundText(caddr(values) 160))
    kind=aiLiveModelViewKind(viewType view)
    unless(and(lib cell view viewType kind) return(nil))
    identity=makeTable("aiLiveModelIdentity" nil)
    identity["library"]=lib
    identity["cell"]=cell
    identity["view"]=view
    identity["view_type"]=viewType
    identity["kind"]=kind
    return(identity)
  )
)

procedure(aiLiveModelTarget(identity)
  list(
    aiLiveModelPair("library" identity["library"])
    aiLiveModelPair("cell" identity["cell"])
    aiLiveModelPair("module" identity["cell"]))
)

procedure(aiLiveModelIdentityPairs(identity)
  list(
    aiLiveModelPair("library" identity["library"])
    aiLiveModelPair("cell" identity["cell"])
    aiLiveModelPair("view" identity["view"])
    aiLiveModelPair("view_type" identity["view_type"])
    aiLiveModelPair("kind" identity["kind"]))
)

procedure(aiLiveModelJsonObject(pairs)
  let((text first pair)
    text="{"
    first=t
    foreach(pair pairs
      unless(first text=strcat(text ","))
      text=strcat(text
        "\"" aiJsonEscape(car(pair)) "\":"
        "\"" aiJsonEscape(cadr(pair)) "\"")
      first=nil)
    strcat(text "}"))
)

procedure(aiLiveModelNextSequence()
  let((value)
    value=or(aiLiveModelState["sequence"] 0)+1
    ; SKILL integer ranges differ between Virtuoso builds. Keep the token
    ; comfortably below the smallest supported fixnum range.
    when(value>2147483647 value=1)
    aiLiveModelState["sequence"]=value
    value
  )
)

; Normalize an optional caller-supplied target table to the JSON pair shape.
procedure(aiLiveModelTargetPairs(target)
  list(
    aiLiveModelPair("library" target["library"])
    aiLiveModelPair("cell" target["cell"])
    aiLiveModelPair("module" target["module"]))
)

procedure(aiLiveModelTargetValidP(target)
  and(tablep(target)
      aiLiveModelBoundText(target["library"] 160)
      aiLiveModelBoundText(target["cell"] 160)
      aiLiveModelBoundText(target["module"] 160))
)

procedure(aiLiveModelViewIdentityValidP(identity)
  and(tablep(identity)
      aiLiveModelBoundText(identity["library"] 160)
      aiLiveModelBoundText(identity["cell"] 160)
      aiLiveModelBoundText(identity["view"] 160)
      aiLiveModelBoundText(identity["view_type"] 160)
      member(identity["kind"] list("schematic" "symbol" "config" "spec" "output_view" "layout")))
)

procedure(aiLiveModelSessionRunningP()
  let((cid alive)
    cid=when(boundp('aiIpcState) aiIpcState["cid"])
    alive=when(cid car(errset(ipcIsAliveProcess(cid) nil)))
    and(boundp('aiIpcState)
        aiIpcState["phase"]=="running"
        aiIpcState["controllerProtocol"]=="control.v2"
        cid alive))
)

procedure(aiLiveModelWriteEvent(payload)
  let((cid encoded result)
    cid=when(boundp('aiIpcState) aiIpcState["cid"])
    encoded=strcat(payload "\n")
    if(and(aiLiveModelSessionRunningP() strlen(encoded)<=65537) then
      result=car(errset(ipcWriteProcess(cid encoded) nil))
      unless(result aiLiveModelState["last_error"]="ipc_write_failed")
      result
    else
      aiLiveModelState["last_error"]="session_unavailable"
      nil)
  )
)

procedure(aiLiveModelPostSaveTrigger(cv)
  prog((identity modified protected sequence generation target payload)
    ; dbRegPostSaveTrigger supplies the saved cellview ID. Be defensive
    ; because callbacks can outlive a closing/reloading editor.
    identity=aiLiveModelIdentity(cv)
    unless(identity
      aiLiveModelState["last_error"]="invalid_cellview"
      return(nil))
    unless(identity["kind"]=="schematic"
      aiLiveModelState["last_error"]="non_schematic_view"
      return(nil))
    protected=errset(dbIsCellViewModified(cv) nil)
    unless(protected
      aiLiveModelState["last_error"]="modified_state_unknown"
      return(nil))
    modified=car(protected)
    when(modified
      aiLiveModelState["last_error"]="cellview_still_modified"
      return(nil))
    unless(aiLiveModelSessionRunningP()
      aiLiveModelState["last_error"]="session_unavailable"
      return(nil))
    sequence=aiLiveModelNextSequence()
    generation=sprintf(nil "save-%d" sequence)
    target=aiLiveModelTarget(identity)
    payload=sprintf(nil
      "{\"event\":\"live_model.source_changed\",\"sequence\":%d,\"source_generation\":\"%s\",\"target\":%s,\"view_identity\":%s,\"changed_kind\":\"%s\"}"
      sequence aiJsonEscape(generation)
      aiLiveModelJsonObject(target)
      aiLiveModelJsonObject(aiLiveModelIdentityPairs(identity))
      aiJsonEscape(identity["kind"]))
    if(aiLiveModelWriteEvent(payload) then
      progn(
        aiLiveModelState["last_sequence"]=sequence
        aiLiveModelState["last_source_generation"]=generation
        aiLiveModelState["last_view_identity"]=identity
        return(t))
    else
      return(nil))
  )
)

procedure(aiLiveModelScanString(line key limit)
  let((needle found rest finish value)
    needle=strcat("\"" key "\":\"")
    found=index(line needle)
    when(found
      rest=substring(found strlen(needle)+1)
      finish=index(rest "\"")
      when(finish
        value=substring(rest 1 strlen(rest)-strlen(finish))
        when(and(stringp(value)
                 strlen(value)<=limit
                 !index(value "\\")) value)))
  )
)

procedure(aiLiveModelScanInteger(line key)
  let((needle found rest finish token value)
    needle=strcat("\"" key "\":")
    found=index(line needle)
    when(found
      rest=substring(found strlen(needle)+1)
      finish=index(rest ",")
      unless(finish finish=index(rest "}"))
      when(finish
        token=substring(rest 1 strlen(rest)-strlen(finish))
        when(rexMatchp("^[0-9]+$" token)
          value=atoi(token)
          when(value<=2147483647 value))))
  )
)

procedure(aiLiveModelForbiddenStatusP(line)
  or(index(line "\"token\"")
     index(line "\"credential\"")
     index(line "\"password\"")
     index(line "\"secret\"")
     index(line "\"verdict\"")
     index(line "\"provider_")
     index(line "\"decision\""))
)

procedure(aiLiveModelHandleStatus(line)
  ; The controller status is a rich notification, not a control request.
  ; Retain bounded scalar fields only; never store raw JSON or result data.
  prog((session state publication source code last eventSequence)
    unless(and(stringp(line)
               strlen(line)<=65536
               index(line "\"event\":\"live_model.status\""))
      return(nil))
    unless(index(line "\"protocol_version\":\"aivw-live-model-v1\"")
      return(nil))
    when(aiLiveModelForbiddenStatusP(line)
      return(nil))
    session=aiLiveModelScanString(line "session_id" 160)
    state=aiLiveModelScanString(line "state" 32)
    publication=aiLiveModelScanString(line "publication" 64)
    source=aiLiveModelScanString(line "source_generation" 256)
    code=aiLiveModelScanString(line "code" 128)
    last=aiLiveModelScanInteger(line "last_sequence")
    eventSequence=aiLiveModelScanInteger(line "event_sequence")
    unless(and(session
               state member(state list("IDLE" "DEBOUNCING" "SNAPSHOTTING"
                                       "GENERATING" "VALIDATING" "AWAITING_APPROVAL"
                                       "STALE" "STOPPED" "ERROR"))
               publication member(publication list("DISABLED"
                                                    "HUMAN_APPROVAL_REQUIRED"
                                                    "REFUSED_NO_PUBLISHER"
                                                    "CANCELLED"))
               numberp(last)
               numberp(eventSequence)
               code)
      return(nil))
    when(and(aiLiveModelState["session_id"]
             session!=aiLiveModelState["session_id"])
      return(nil))
    aiLiveModelState["session_id"]=session
    aiLiveModelState["state"]=state
    aiLiveModelState["publication"]=publication
    when(source aiLiveModelState["source_generation"]=source)
    when(code aiLiveModelState["last_status_code"]=code)
    aiLiveModelState["last_status_sequence"]=last
    when(eventSequence aiLiveModelState["event_sequence"]=eventSequence)
    return(t)
  )
)

procedure(aiLiveModelStart(recipe through profile @optional debounceMs target viewIdentity)
  prog((cid delay payload targetPayload viewPayload)
    unless(and(aiLiveModelBoundText(recipe 160)
               aiLiveModelBoundText(through 160)
               aiLiveModelBoundText(profile 160))
      return(nil))
    when(target
      unless(aiLiveModelTargetValidP(target) return(nil)))
    when(viewIdentity
      unless(aiLiveModelViewIdentityValidP(viewIdentity) return(nil))
      when(target
        unless(and(target["library"]==viewIdentity["library"]
                   target["cell"]==viewIdentity["cell"])
          return(nil))))
    unless(aiLiveModelSessionRunningP() return(nil))
    cid=aiIpcState["cid"]
    delay=if(and(numberp(debounceMs) debounceMs>=0 debounceMs<=10000)
      then fix(debounceMs) else 750)
    targetPayload=when(target aiLiveModelJsonObject(aiLiveModelTargetPairs(target)))
    viewPayload=when(viewIdentity aiLiveModelJsonObject(aiLiveModelIdentityPairs(viewIdentity)))
    payload=strcat(
      sprintf(nil
        "{\"event\":\"live_model.start\",\"recipe_id\":\"%s\",\"through\":\"%s\",\"profile\":\"%s\",\"debounce_ms\":%d"
        aiJsonEscape(recipe) aiJsonEscape(through) aiJsonEscape(profile) delay)
      if(targetPayload then strcat(",\"target\":" targetPayload) else "")
      if(viewPayload then strcat(",\"view_identity\":" viewPayload) else "")
      "}")
    aiLiveModelState["session_id"]=nil
    aiLiveModelState["state"]="STARTING"
    aiLiveModelState["sequence"]=0
    aiLiveModelState["last_error"]=nil
    when(aiIpcWrite(cid payload) return(cid))
    return(nil)
  )
)

procedure(aiLiveModelChanged(cv)
  aiLiveModelPostSaveTrigger(cv)
)

procedure(aiLiveModelStop()
  when(aiLiveModelSessionRunningP()
    aiIpcWrite(aiIpcState["cid"] "{\"event\":\"live_model.stop\"}"))
  aiLiveModelState["state"]="STOPPED"
  t
)

procedure(aiLiveModelStatus()
  when(aiLiveModelSessionRunningP()
    aiIpcWrite(aiIpcState["cid"] "{\"event\":\"live_model.status\"}"))
  aiLiveModelState
)

procedure(aiLiveModelReset()
  aiLiveModelState=makeTable("aiLiveModelState" nil)
  t
)

; Replace a stale registration on an additive development reload. Context
; builds defer session registrations to aiRuntimeInitialize.
unless(and(boundp('cadContextBuilding) cadContextBuilding)
  when(isCallable('dbUnregPostSaveTrigger)
    errset(dbUnregPostSaveTrigger('aiLiveModelPostSaveTrigger) nil))
  when(isCallable('dbRegPostSaveTrigger)
    errset(dbRegPostSaveTrigger('aiLiveModelPostSaveTrigger 0) nil)))

aiLiveModelVersion="20260902.ai.live.v1"
)
