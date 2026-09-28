procedure(mtsDefaultsJsonEscape(value)
  let((s out i ch)
    s=if(value then sprintf(nil "%s" value) else "")
    out=""
    for(i 1 strlen(s)
      ch=substring(s i 1)
      case(ch
        ("\\" out=strcat(out "\\\\"))
        ("\"" out=strcat(out "\\\""))
        ("\n" out=strcat(out "\\n"))
        ("\r" out=strcat(out "\\r"))
        ("\t" out=strcat(out "\\t"))
        (t out=strcat(out ch))
      )
    )
    out
  )
)
procedure(mtsDefaultsJsonString(value)
  strcat("\"" mtsDefaultsJsonEscape(value) "\"")
)
procedure(mtsDefaultsJsonValue(value)
  cond(
    (null(value) "null")
    (eq(value t) "true")
    (numberp(value) sprintf(nil "%L" value))
    (stringp(value) mtsDefaultsJsonString(value))
    (symbolp(value) mtsDefaultsJsonString(get_pname(value)))
    (listp(value)
      let((out sep item)
        out="[" sep=""
        foreach(item value
          out=strcat(out sep mtsDefaultsJsonValue(item))
          sep=","
        )
        strcat(out "]")
      )
    )
    (t mtsDefaultsJsonString(sprintf(nil "%L" value)))
  )
)
procedure(mtsDefaultsJsonArrayValue(value)
  if(value then mtsDefaultsJsonValue(value) else "[]")
)
procedure(mtsDefaultsJsonObject(items)
  let((out sep item)
    out="{" sep=""
    foreach(item items
      out=strcat(out sep mtsDefaultsJsonString(car(item)) ":" mtsDefaultsJsonValue(cadr(item)))
      sep=","
    )
    strcat(out "}")
  )
)
procedure(mtsDefaultsSafeEnv(tool name)
  let((answer)
    answer=errset(asiGetEnvOptionVal(tool name) nil)
    if(answer then car(answer) else nil)
  )
)
procedure(mtsDefaultsSafeSim(tool name)
  let((answer)
    answer=errset(asiGetSimOptionVal(tool name) nil)
    if(answer then car(answer) else nil)
  )
)
procedure(mtsDefaultsSafeChoices(scope name)
  let((answer)
    answer=errset(asiGetSimOptionChoices(scope name) nil)
    if(answer then car(answer) else nil)
  )
)
procedure(mtsDefaultsOptionNames(tool)
  let((answer)
    answer=errset(asiGetSimOptionNameList(tool) nil)
    if(answer then car(answer) else nil)
  )
)
procedure(mtsDefaultsSafeCurrentSession()
  let((answer)
    answer=errset(asiGetCurrentSession() nil)
    if(answer then car(answer) else nil)
  )
)
procedure(mtsDefaultsSafeModelSelections(session)
  let((answer)
    answer=if(session then errset(asiGetModelLibSelectionList(session) nil) else nil)
    if(answer then car(answer) else nil)
  )
)
procedure(mtsDefaultsModelSelectionRows(session)
  let((selections out item file section answer)
    selections=mtsDefaultsSafeModelSelections(session) out=nil
    foreach(item selections
      answer=errset(asiGetModelLibFile(item) nil)
      file=if(answer then car(answer) else if(listp(item) then car(item) else nil))
      answer=errset(asiGetModelLibSection(item) nil)
      section=if(answer then car(answer) else if(and(listp(item) cdr(item)) then cadr(item) else ""))
      when(file out=cons(list(file section) out))
    )
    reverse(out)
  )
)
procedure(mtsDefaultsScopeValue(session tool getter name)
  let((answer fallback)
    answer=if(session then errset(funcall(getter session name) nil) else nil)
    if(answer then
      car(answer)
      else
        fallback=errset(funcall(getter tool name) nil)
        if(fallback then car(fallback) else nil)
    )
  )
)
procedure(mtsDefaultsSnapshot(tool session)
  let((simItems names name value choices modelSelections rawModelFiles modelFiles source scope)
    simItems=nil names=mtsDefaultsOptionNames(tool)
    foreach(name names
      value=mtsDefaultsScopeValue(session tool 'asiGetSimOptionVal name)
      choices=mtsDefaultsScopeValue(session tool 'asiGetSimOptionChoices name)
      value=list(list("value" value) list("choices" choices))
      simItems=cons(list(sprintf(nil "%s" name) value) simItems)
    )
    modelSelections=mtsDefaultsModelSelectionRows(session)
    rawModelFiles=mtsDefaultsScopeValue(session tool 'asiGetEnvOptionVal 'modelFiles)
    modelFiles=if(rawModelFiles then rawModelFiles else modelSelections)
    source=if(rawModelFiles then "modelFiles" else if(modelSelections then "asiGetModelLibSelectionList" else "none"))
    scope=if(session then "session" else "tool")
    strcat("{\"model_files\":" mtsDefaultsJsonArrayValue(modelFiles)
           ",\"raw_model_files\":" mtsDefaultsJsonArrayValue(rawModelFiles)
           ",\"model_files_source\":" mtsDefaultsJsonString(source)
           ",\"option_scope\":" mtsDefaultsJsonString(scope)
           ",\"session_available\":" mtsDefaultsJsonValue(if(session then t else nil))
           ",\"environment_options\":{}"
           ",\"simulator_options\":" mtsDefaultsJsonObject(reverse(simItems)) "}")
  )
)
procedure(mtsDefaultsMaePairs(items)
  let((out item key value)
    out=nil
    while(items
      item=car(items)
      if(listp(item) then
        progn(
          ; Some releases return ((name value) ...), while the
          ; documented MAE form is (name value name value ...).
          when(cdr(item)
            out=cons(list(mtsDefaultsMaeKeyText(car(item)) cadr(item)) out)
          )
          items=cdr(items)
        )
      else
        progn(
          key=item
          items=cdr(items)
          when(items
            value=car(items)
            out=cons(list(mtsDefaultsMaeKeyText(key) value) out)
            items=cdr(items)
          )
        )
      )
    )
    reverse(out)
  )
)
procedure(mtsDefaultsMaeKeyText(value)
  let((text)
    text=if(symbolp(value) then get_pname(value)
          else if(stringp(value) then value
               else sprintf(nil "%L" value)))
    when(and(text strlen(text)>0 substring(text strlen(text) 1)==":")
      text=substring(text 1 strlen(text)-1)
    )
    text
  )
)
procedure(mtsDefaultsMaeLookup(items wanted)
  let((answer item key wantedText)
    answer=nil
    wantedText=mtsDefaultsMaeKeyText(wanted)
    foreach(item items
      when(item && listp(item)
        key=car(item)
        when(equal(mtsDefaultsMaeKeyText(key) wantedText)
          answer=cadr(item)
        )
      )
    )
    answer
  )
)
procedure(mtsDefaultsMaeSafeEnv(session test)
  let((answer errorInfo value)
    answer=errset(maeGetEnvOption(test ?includeEmpty t ?session session) nil), errorInfo=errset.errset
    if(answer then
      progn(
        value=car(answer)
        if(value then
          list(t value nil nil)
        else
          list(t nil nil "maeGetEnvOption returned nil")
        )
      )
    else
      list(nil nil strcat("maeGetEnvOption raised an exception: "
                          sprintf(nil "%L" errorInfo)) nil)
    )
  )
)
procedure(mtsDefaultsMaeSafeSim(session test)
  let((answer errorInfo value)
    answer=errset(maeGetSimOption(test ?includeEmpty t ?session session) nil), errorInfo=errset.errset
    if(answer then
      progn(
        value=car(answer)
        if(value then
          list(t value nil nil)
        else
          list(t nil nil "maeGetSimOption returned nil")
        )
      )
    else
      list(nil nil strcat("maeGetSimOption raised an exception: "
                          sprintf(nil "%L" errorInfo)) nil)
    )
  )
)
procedure(mtsDefaultsMaeSnapshot(session test)
  let((envResult simResult env sim envOk simOk envError simError envDiagnostic simDiagnostic
       envPairs simPairs modelFiles simObject envObject errors diagnostics)
    envResult=mtsDefaultsMaeSafeEnv(session test)
    simResult=mtsDefaultsMaeSafeSim(session test)
    envOk=car(envResult) env=cadr(envResult) envError=caddr(envResult)
    simOk=car(simResult) sim=cadr(simResult) simError=caddr(simResult)
    envDiagnostic=cadddr(envResult) simDiagnostic=cadddr(simResult)
    envPairs=if(envOk then mtsDefaultsMaePairs(env) else nil)
    simPairs=if(simOk then mtsDefaultsMaePairs(sim) else nil)
    modelFiles=mtsDefaultsMaeLookup(envPairs 'modelFiles)
    envObject=if(envPairs then envPairs else nil)
    simObject=if(simPairs then simPairs else nil)
    errors=nil
    diagnostics=nil
    unless(envOk errors=cons(envError errors))
    unless(simOk errors=cons(simError errors))
    when(envDiagnostic diagnostics=cons(envDiagnostic diagnostics))
    when(simDiagnostic diagnostics=cons(simDiagnostic diagnostics))
    list(strcat("{\"model_files\":" mtsDefaultsJsonArrayValue(modelFiles)
           ",\"environment_options\":" mtsDefaultsJsonObject(envObject)
           ",\"simulator_options\":" mtsDefaultsJsonObject(simObject) "}"),
         reverse(errors) reverse(diagnostics))
  )
)
procedure(mtsDefaultsMaeClose(session)
  let((answer errorInfo value)
    answer=errset(maeCloseSession(?session session) nil), errorInfo=errset.errset
    if(answer then
      progn(
        value=car(answer)
        if(value then nil else "maeCloseSession returned nil")
      )
    else
      strcat("maeCloseSession raised an exception: "
             sprintf(nil "%L" errorInfo))
    )
  )
)
procedure(mtsDefaultsWorkerReady() t)
