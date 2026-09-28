; Development bootstrap. Production loads protected contexts only.
let((root)
  root=getShellEnvVar("SICO_HOME")
  unless(root
    when(getShellEnvVar("CAD_HOME") error("CAD_HOME was removed; set SICO_HOME")))
  unless(and(root strlen(root)>0 substring(root 1 1)=="/")
    error("SICO_HOME must name an absolute installation\n"))
  load(strcat(root "/tools/common/skill/SICO_environment.il"))
  load(strcat(root "/tools/common/skill/SICO_installation.il")))

;------------------------------------------------------------------------------
; Canonical source for CAD_HOME/scripts/sicoAutoLoad.il.
; CAD_HOME/tools/scripts is reserved for temporary SKILL sources.
;------------------------------------------------------------------------------

unless(and(boundp('sicoAutoLoadVersion)
             sicoAutoLoadVersion=="20260921.sico.autoload.v1"
             isCallable('sicoAutoLoadSkill))

procedure(sicoAutoLoadSkill()
  let((getDirMembers loadFile underRoot loadTree visited loadedFiles failures
        commonReady cadHome toolsRoot toolsCanonical registerFile projCadHome)
    visited=makeTable("sicoAutoLoadVisited" nil)
    loadedFiles=makeTable("sicoAutoLoadFiles" nil)
    failures=0
    commonReady=nil

    getDirMembers=lambda((dir)
      let((result members detail)
        result=errset(getDirFiles(dir) nil)
        if(result then
          members=car(result)
          sort(
            setof(item members
              and(not(member(item '("." "..")))
                   not(rexMatchp("^[.]" item))))
            'alphalessp)
        else
          detail=errset.errset
          failures=failures+1
          warn("[CAD] Cannot read SKILL directory %s: %L\n" dir detail)
          nil
        )
      )
    )

    loadFile=lambda((fullPath)
      let((result detail)
        info("         %s  ----  " fullPath)
        result=errset(load(fullPath) nil)
        if(result then
          info("SUCCEED\n")
        else
          detail=errset.errset
          failures=failures+1
          info("FAILED\n")
          warn("[CAD] Failed to load %s: %L\n" fullPath detail)
        )
        result
      )
    )

    underRoot=lambda((path root)
      let((pathLength rootLength)
        pathLength=strlen(path)
        rootLength=strlen(root)
        or(path==root
            and(pathLength>rootLength
                 substring(path 1 rootLength)==root
                 substring(path rootLength+1 1)=="/"))
      )
    )

    ; Project trees contain public classic-SKILL entry files only. SKILL++
    ; implementation files are loaded by their owning entry point.
    loadTree=lambda((dir)
      let((canonical fullPath canonicalFile result)
        canonical=simplifyFilename(dir)
        cond(
          (and(toolsCanonical
                apply(underRoot list(canonical toolsCanonical)))
            info("         Skipping common CAD tools subtree: %s\n" dir))
          (visited[canonical]
            nil)
          (t
            visited[canonical]=t
            foreach(dirMember apply(getDirMembers list(dir))
              fullPath=strcat(dir "/" dirMember)
              cond(
                (isDir(fullPath)
                  apply(loadTree list(fullPath)))
                (and(isFile(fullPath)
                      rexMatchp("[.]il$" dirMember)
                      not(member(dirMember
                                   '("cdsLibMgr.il" "cadLoadThisFirst.il"
                                     "loadThisFirst.il" "sicoToolRegister.il"
                                     "sicoAutoLoad.il"))))
                  canonicalFile=simplifyFilename(fullPath)
                  unless(loadedFiles[canonicalFile]
                    result=apply(loadFile list(fullPath))
                    when(result
                      loadedFiles[canonicalFile]=t)
                  ))
              )
            )
          )
        )
      )
    )

    cadHome=SICO_installationRoot()
    info("<INFO> Registering common CAD tools:\n")
    if(and(cadHome strlen(cadHome)>0) then
      toolsRoot=simplifyFilename(strcat(cadHome "/tools") t)
      toolsCanonical=simplifyFilename(toolsRoot)
      registerFile=strcat(toolsRoot "/common/skill/sicoToolRegister.il")
      if(isFile(registerFile) then
        when(apply(loadFile list(registerFile))
          if(and(boundp('sicoToolRegisterVersion)
                   boundp('sicoToolRegisterInitVersion)
                   sicoToolRegisterInitVersion==sicoToolRegisterVersion
                   boundp('sicoToolRegisterInitRoot)
                   sicoToolRegisterInitRoot==toolsCanonical
                   isCallable('cadLoadRelative)
                   isCallable('sicoToolRegister)
                   isCallable('cadLoadToolScripts)
                   isCallable('witMenuLoadRelative)
                   isCallable('witMenuReload)) then
            commonReady=t
          else
            failures=failures+1
            warn("[CAD] Common CAD tool registration did not complete.\n")
          )
        )
      else
        failures=failures+1
        warn("[CAD] Cannot find common CAD tool register: %s\n" registerFile)
      )
    else
      failures=failures+1
      warn("[CAD] CAD_HOME is not set; common CAD tools were not loaded.\n")
    )

    projCadHome=getShellEnvVar("PROJ_CAD_HOME")
    info("<INFO> Loading project CAD skill files:\n")
    cond(
      (not(commonReady)
        info("         Common CAD registration failed; skipping project SKILL.\n"))
      (not(and(projCadHome strlen(projCadHome)>0))
        info("         PROJ_CAD_HOME is not set; skipping project SKILL.\n"))
      (not(isDir(projCadHome))
        failures=failures+1
        warn("[CAD] PROJ_CAD_HOME is not a directory: %s\n" projCadHome))
      (and(toolsCanonical
            simplifyFilename(projCadHome)==toolsCanonical)
        failures=failures+1
        warn("[CAD] PROJ_CAD_HOME must not point to CAD_HOME/tools: %s\n"
              projCadHome))
      (t
        apply(loadTree list(projCadHome)))
    )

    failures==0
  )
)

sicoAutoLoadVersion="20260921.sico.autoload.v1"
)

unless(and(boundp('sicoAutoLoadInitVersion)
             sicoAutoLoadInitVersion=="20260921.sico.autoload.v1")
  if(sicoAutoLoadSkill() then
    sicoAutoLoadInitVersion="20260921.sico.autoload.v1"
  else
    warn("[CAD] CAD SKILL auto-load completed with errors.\n")
  )
)
