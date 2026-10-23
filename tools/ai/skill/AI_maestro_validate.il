; Maestro setup values and preflight validation.
; Loaded with AI_maestro.il by the versioned AI parent.
unless(and(boundp('aiMaestroValidationVersion)
             aiMaestroValidationVersion=="20260908.ai.maestro.v2")

procedure(aiMaestroBoundText(value limit)
  let((text)
    text=cond((null(value) "") (stringp(value) value)
               (t sprintf(nil "%L" value)))
    if(strlen(text)>limit then substring(text 1 limit) else text)))
procedure(aiMaestroJsonString(value @optional limit)
  sprintf(nil "\"%s\"" aiJsonEscape(aiMaestroBoundText(value or(limit 2048)))))
procedure(aiMaestroValueJson(value depth)
  cond((null(value) "null") ((value==t) "true")
        (stringp(value) aiMaestroJsonString(value 2048))
        (numberp(value) sprintf(nil "%L" value))
        (symbolp(value) aiMaestroJsonString(value 128))
        (and(listp(value) depth<4) aiMaestroListJson(value depth+1))
        (t aiMaestroJsonString(value 256))))
procedure(aiMaestroListJson(values depth)
  let(((body "") (count 0) separator)
    foreach(value values
      when(count<64
        separator=if(count>0 then "," else "")
        body=strcat(body separator aiMaestroValueJson(value depth)))
      count=count+1)
    when(count>64 body=strcat(body ",\"...truncated...\""))
    strcat("[" body "]")))
procedure(aiMaestroError(code message @optional jobId)
  if(jobId then
    sprintf(nil "{\"ok\":false,\"job_id\":%s,\"error\":{\"code\":%s,\"message\":%s}}"
      aiMaestroJsonString(jobId 96) aiMaestroJsonString(code 96) aiMaestroJsonString(message 1024))
  else sprintf(nil "{\"ok\":false,\"error\":{\"code\":%s,\"message\":%s}}"
    aiMaestroJsonString(code 96) aiMaestroJsonString(message 1024))))
procedure(aiMaestroTextP(value limit)
  let(((ok t) index code)
    if(or(!stringp(value) strlen(value)<1 strlen(value)>limit) then nil
    else
      for(index 1 strlen(value)
        code=charToInt(concat(substring(value index 1)))
        when(or(code<32 code==127) ok=nil))
      ok)))
procedure(aiMaestroBooleanP(value) or(null(value) value==t))
procedure(aiMaestroDataP(value depth)
  let(((ok t) (count 0))
    cond((stringp(value) strlen(value)<=8192)
          (or(numberp(value) symbolp(value) null(value)) t)
          (and(listp(value) depth<4)
            foreach(item value
              count=count+1
              unless(and(count<=64 aiMaestroDataP(item depth+1)) ok=nil))
            ok)
          (t nil))))
procedure(aiMaestroPairListP(pairs @optional allowedKeys)
  let(((ok listp(pairs)) (count 0))
    when(ok foreach(pair pairs
      count=count+1
      unless(and(count<=64 listp(pair) length(pair)==2
                   aiMaestroTextP(car(pair) 256)
                   or(!allowedKeys member(car(pair) allowedKeys))
                   aiMaestroDataP(cadr(pair) 0)) ok=nil)))
    ok))
procedure(aiMaestroStringListP(values)
  let(((ok listp(values)) (count 0))
    when(ok foreach(value values
      count=count+1
      unless(and(count<=64 aiMaestroTextP(value 256)) ok=nil)))
    ok))
procedure(aiMaestroVarListP(vars)
  let(((ok listp(vars)) (count 0))
    when(ok foreach(var vars
      count=count+1
      unless(and(count<=64 listp(var) length(var)==2
                   aiMaestroTextP(car(var) 256) aiMaestroDataP(cadr(var) 0))
        ok=nil)))
    ok))
procedure(aiMaestroFileP(path)
  and(aiMaestroTextP(path 4096) substring(path 1 1)=="/" isFile(path)))
procedure(aiMaestroValidateCreate(setupLib setupCell setupView policy testName
             tbLib tbCell tbView simulator analyses vars modelFiles stimuli
             outputs corners)
  prog((entry type signal expr specs cornerVars)
    unless(and(aiMaestroTextP(setupLib 256) aiMaestroTextP(setupCell 256)
                 aiMaestroTextP(setupView 256) aiMaestroTextP(testName 256)
                 aiMaestroTextP(tbLib 256) aiMaestroTextP(tbCell 256)
                 aiMaestroTextP(tbView 256) aiMaestroTextP(simulator 256))
      return("library, cell, view, test, and simulator names are invalid"))
    unless(member(policy list("error" "update"))
      return("policy must be error or update"))
    unless(and(listp(analyses) length(analyses)>=1 length(analyses)<=32)
      return("analyses must contain between 1 and 32 entries"))
    foreach(entry analyses
      unless(and(listp(entry) length(entry)==3
                   member(car(entry) list("dc" "ac" "tran"))
                   aiMaestroBooleanP(cadr(entry)) aiMaestroPairListP(caddr(entry)))
        return("each analysis must be (dc/ac/tran enabled optionsPairs)")))
    unless(aiMaestroVarListP(vars)
      return("vars must contain at most 64 (name value) entries"))
    unless(and(listp(modelFiles) length(modelFiles)<=64)
      return("modelFiles must contain at most 64 entries"))
    foreach(entry modelFiles
      unless(or(and(stringp(entry) aiMaestroFileP(entry))
                  and(listp(entry) length(entry)==2 aiMaestroFileP(car(entry))
                       aiMaestroTextP(cadr(entry) 256)))
        return("each model file must be an existing absolute path or (path section)")))
    unless(and(listp(stimuli) length(stimuli)<=64)
      return("stimuli must contain at most 64 paths"))
    foreach(entry stimuli
      unless(aiMaestroFileP(entry)
        return("each stimulus must be an existing absolute file")))
    unless(and(listp(outputs) length(outputs)<=64)
      return("outputs must contain at most 64 entries"))
    foreach(entry outputs
      unless(and(listp(entry) length(entry)==7 aiMaestroTextP(car(entry) 256))
        return("each output must be (name type signal/nil expr/nil plot save specs)"))
      type=cadr(entry) signal=caddr(entry) expr=cadddr(entry) specs=nth(6 entry)
      unless(and(member(type list("point" "corners" "sweeps" "all"
                                    "terminal" "terminalV" "net"))
                   aiMaestroBooleanP(nth(4 entry)) aiMaestroBooleanP(nth(5 entry))
                   aiMaestroPairListP(specs
                     list("minimum" "maximum" "gt" "lt" "tolerance")))
        return("output type, plot/save, or specs are invalid"))
      if(member(type list("terminal" "terminalV" "net")) then
        unless(and(aiMaestroTextP(signal 4096) null(expr))
          return("terminal and net outputs require only a signal"))
      else
        unless(and(null(signal) aiMaestroTextP(expr 8192))
          return("evaluated outputs require only an expression"))))
    unless(and(listp(corners) length(corners)<=64)
      return("corners must contain at most 64 entries"))
    foreach(entry corners
      unless(and(listp(entry) length(entry)==5 aiMaestroTextP(car(entry) 256)
                   aiMaestroBooleanP(cadr(entry)) aiMaestroStringListP(caddr(entry))
                   aiMaestroStringListP(cadddr(entry)))
        return("each corner must be (name enabled enableTests disableTests vars)"))
      foreach(test caddr(entry)
        when(member(test cadddr(entry))
          return("a corner cannot enable and disable the same test")))
      cornerVars=nth(4 entry)
      unless(aiMaestroVarListP(cornerVars)
        return("corner variables must contain (name value) entries")))
    nil))
aiMaestroValidationVersion="20260908.ai.maestro.v2"
)
