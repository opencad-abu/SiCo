;------------------------------------------------------------------------------
; Shared two-column TSV and standalone PyQt launch helpers.
;------------------------------------------------------------------------------

unless(and(boundp('sicoGuiProtocolVersion)
             sicoGuiProtocolVersion=="20260814.cadgui.v1")

procedure(SICO_guiProtocolValidField(value)
  and(stringp(value) strlen(value)>0
      !index(value "\t") !index(value "\r") !index(value "\n"))
)

procedure(SICO_guiProtocolWrite(path records @optional status version)
  let((kind ok output record value)
    output=outfile(path "w")
    if(!output then
      nil
    else
      ok=t
      when(version
        unless(and(SICO_guiProtocolValidField(version)
                   fprintf(output "#version\t%s\n" version))
          ok=nil))
      when(status
        unless(and(SICO_guiProtocolValidField(status)
                   fprintf(output "#status\t%s\n" status))
          ok=nil))
      foreach(record or(records nil)
        if(and(listp(record) length(record)==2) then
          kind=car(record)
          value=cadr(record)
          unless(and(SICO_guiProtocolValidField(kind)
                     SICO_guiProtocolValidField(value)
                     fprintf(output "%s\t%s\n" kind value))
            ok=nil)
        else
          ok=nil))
      close(output)
      ok
    )
  )
)

procedure(SICO_guiProtocolRead(path allowedKinds @optional requiredStatus)
  prog((fields input kind line records status valid value version)
    status=nil
    version=nil
    valid=t
    unless(and(path isFile(path))
      return(list(nil nil nil nil)))
    input=infile(path)
    while(gets(line input)
      while(and(strlen(line)>0
                member(substring(line strlen(line) 1) list("\r" "\n")))
        line=substring(line 1 strlen(line)-1))
      if(line=="" then
        nil
      else
        if(and(substring(line 1 1)=="#" !index(line "\t")) then
          nil
        else
          fields=parseString(line "\t" t)
          if(length(fields)!=2 then
            valid=nil
          else
            kind=car(fields)
            value=cadr(fields)
            if(!SICO_guiProtocolValidField(kind) ||
               !SICO_guiProtocolValidField(value) then
              valid=nil
            else
              case(kind
                ("#status"
                  if(status then valid=nil else status=value))
                ("#version"
                  if(version then valid=nil else version=value))
                (t
                  if(substring(kind 1 1)=="#" then
                    nil
                  else
                    if(member(kind allowedKinds) then
                      records=append1(records list(kind value))
                    else
                      valid=nil)))))))))
    close(input)
    when(and(requiredStatus status!=requiredStatus)
      valid=nil)
    return(list(valid records status version))
  )
)

procedure(SICO_guiPythonEnvironment(originalLdName @optional extraUnset)
  let((environment)
    environment=SICO_tempEnvironment()
    when(and(environment stringp(originalLdName)
             rexMatchp("^[A-Za-z_][A-Za-z0-9_]*$" originalLdName))
      strcat("exec env -u PYTHONHOME -u PYTHONPATH "
             "-u QT_QPA_PLATFORM_PLUGIN_PATH -u QT_PLUGIN_PATH "
             "-u QT_DIR -u QT_SELECT -u QML2_IMPORT_PATH "
             "-u QT_XCB_NO_XI2 -u QT_XCB_NO_XI2_MOUSE "
             or(extraUnset "")
             environment
             originalLdName "="
             SICO_shellQuote(or(getShellEnvVar("LD_LIBRARY_PATH") ""))
             " LD_LIBRARY_PATH=" SICO_shellQuote(SICO_pythonLibraryPath())
             " "))
  )
)

procedure(SICO_guiPythonLaunchCommand(args originalLdName @optional extraUnset)
  let((environment)
    environment=SICO_guiPythonEnvironment(originalLdName extraUnset)
    when(environment
      strcat(environment args))
  )
)

procedure(SICO_guiProtocolRevision()
  "20260814.cadgui.v1"
)

sicoGuiProtocolVersion="20260814.cadgui.v1"
)
