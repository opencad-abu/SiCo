;------------------------------------------------------------------------------
; SiCo CAD tool registration entry point.
; Loaded by scripts/sicoAutoLoad.il after installation contract initialization.
;------------------------------------------------------------------------------

unless(and(boundp('sicoToolRegisterVersion)
             sicoToolRegisterVersion=="20260922.sico.register.v3"
             isCallable('cadLoadRelative)
             isCallable('cadLoadToolScripts)
             isCallable('sicoToolRegister))

procedure(cadResolveToolsRoot()
  let((sicoHome)
    sicoHome=SICO_installationRoot()
    setShellEnvVar("SICO_HOME" sicoHome)
    SICO_installationPath(sicoHome "tools")))

procedure(cadLoadFile(path)
  if(and(path isFile(path)) then
    load(path)
  else
    warn("%s CAD loader cannot find: %L\n"
         cond(
           (isCallable('SICO_logo) SICO_logo())
           (isCallable('witMenuLogo) witMenuLogo())
           (t "SiCo"))
         path)
    nil
  )
)

procedure(cadPath(relativePath)
  if(cadToolsRoot then
    simplifyFilename(strcat(cadToolsRoot "/" relativePath) t)
  else
    warn("CAD tools root is not configured. Set SICO_HOME before loading CAD tools.\n")
    nil
  )
)

procedure(cadLoadRelative(relativePath)
  cadLoadFile(cadPath(relativePath))
)

procedure(cadToolScriptSourceP(name)
  or(rexMatchp("[.]il$" name)
      rexMatchp("[.]ils$" name))
)

procedure(cadLoadToolScripts()
  let((scriptsRoot visited ok recurse)
    ; SICO_HOME/tools/scripts holds temporary sources only. The production
    ; bootstrap remains SICO_HOME/scripts/sicoAutoLoad.il.
    scriptsRoot=cadPath("scripts")
    if(not(and(scriptsRoot isDir(scriptsRoot))) then
      t
    else
      unless(boundp('cadToolScriptLoadTable)
        cadToolScriptLoadTable=makeTable("cadToolScriptLoadTable" nil)
      )
      visited=makeTable("cadToolScriptVisited" nil)
      ok=t
      recurse=lambda((dir)
        let((canonical result entries fullPath canonicalFile detail)
          canonical=simplifyFilename(dir)
          unless(visited[canonical]
            visited[canonical]=t
            result=errset(getDirFiles(dir) nil)
            if(result then
              entries=sort(
                setof(item car(result)
                  and(not(member(item '("." "..")))
                       not(rexMatchp("^[.]" item))))
                'alphalessp)
              foreach(entry entries
                fullPath=strcat(dir "/" entry)
                cond(
                  (isDir(fullPath)
                    apply(recurse list(fullPath)))
                  (and(isFile(fullPath) cadToolScriptSourceP(entry))
                    canonicalFile=simplifyFilename(fullPath)
                    unless(cadToolScriptLoadTable[canonicalFile]
                      info("<INFO> Loading CAD tool script: %s\n" fullPath)
                      result=errset(load(fullPath) nil)
                      if(result then
                        cadToolScriptLoadTable[canonicalFile]=t
                      else
                        detail=errset.errset
                        ok=nil
                        warn("Failed to load CAD tool script %s: %L\n"
                              fullPath detail)
                      )
                    )
                  )
                )
              )
            else
              detail=errset.errset
              ok=nil
              warn("Cannot read CAD tool scripts directory %s: %L\n"
                    dir detail)
            )
          )
        )
      )
      apply(recurse list(scriptsRoot))
      ok
    )
  )
)

procedure(sicoToolRegister()
  let((menuRoot loaded menuReady scriptsReady)
    menuRoot=cadPath("utility")
    witMenuRoot=menuRoot
    loaded=cadLoadRelative("utility/menu.il")
    menuReady=and(loaded
                   boundp('witMenuInitVersion)
                   witMenuInitVersion=="20260922.sico.menu.v4"
                   boundp('witMenuInitRoot)
                   witMenuInitRoot==menuRoot)
    scriptsReady=and(menuReady cadLoadToolScripts())
    and(menuReady scriptsReady)
  )
)

sicoToolRegisterVersion="20260922.sico.register.v3"
)

cadToolsRoot=cadResolveToolsRoot()
when(and(cadToolsRoot
           or(not(boundp('sicoToolRegisterInitVersion))
               sicoToolRegisterInitVersion!="20260922.sico.register.v3"
               not(boundp('sicoToolRegisterInitRoot))
               sicoToolRegisterInitRoot!=cadToolsRoot))
  when(sicoToolRegister()
    sicoToolRegisterInitRoot=cadToolsRoot
    sicoToolRegisterInitVersion="20260922.sico.register.v3"
  )
)
