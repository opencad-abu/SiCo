; Fixed project readers for Copilot. Tokens are validated data, never read/eval source.
procedure(sicoProjectReply(aerCid aerFields)
  prog((aerTarget aerProtected aerPayload aerCode)
    aerCode="stale_context"
    aerProtected=errset(progn(
      unless(member(length(aerFields) list(4 6 7)) error("Invalid project envelope"))
      aerTarget=sicoSessionTarget(aerCid caddr(aerFields))
      unless(and(aerTarget isCallable('aiEntryProjectRead)) error("Project target unavailable"))
      sicoProjectContextJson(aerTarget)
      unless(aerTarget['projectValid] error("Project target stale"))
      aerCode="read_failed"
      aiEntryProjectRead(aerTarget cadr(aerFields) cdddr(aerFields))) nil)
    aerPayload=when(aerProtected car(aerProtected))
    when(and(aerPayload strlen(aerPayload)>60000) aerPayload=nil aerCode="result_too_large")
    return(if(aerPayload then
      sprintf(nil "{\"id\":\"%s\",\"ok\":true,\"result\":%s}\n" car(aerFields) aerPayload)
      else sprintf(nil "{\"id\":\"%s\",\"ok\":false,\"code\":\"%s\"}\n" car(aerFields) aerCode)))))
procedure(aiEntryProjectName(aepName)
  and(stringp(aepName) strlen(aepName)>0 strlen(aepName)<=96
    rexMatchp("^[A-Za-z_][A-Za-z0-9_.@-]*$" aepName)))
procedure(aiEntryProjectRead(aepBinding aepMethod aepFields)
  prog((aepData aepTargets aepLibraries aepParts aepPrefix)
    unless(and(isCallable('aiProjectSession) isCallable('aiProjectObserve)
      isCallable('aiProjectReceipt)) error("Project readers unavailable"))
    sicoProjectContextJson(aepBinding)
    unless(aepBinding['projectValid] error("Task project changed"))
    case(aepMethod
      ("project_session"
        unless(and(length(aepFields)==1 strlen(car(aepFields))==32
          rexMatchp("^[a-f0-9]+$" car(aepFields))) error("Invalid session candidate"))
        aepData=aiProjectSession(car(aepFields)))
      ("project_observe"
        unless(and(length(aepFields)==3 member(car(aepFields) list("foreground" "background")))
          error("Invalid project observation"))
        foreach(aepRow parseString(cadr(aepFields) ";")
          aepParts=parseString(aepRow ",")
          unless(and(length(aepParts)==3 equal(buildString(aepParts ",") aepRow)
            forall(aepName aepParts aiEntryProjectName(aepName))) error("Invalid project target"))
          aepTargets=cons(aepParts aepTargets))
        aepTargets=reverse(aepTargets) aepLibraries=parseString(caddr(aepFields) ",")
        unless(and(length(aepTargets)>0 length(aepTargets)<=32 length(aepLibraries)>0
          length(aepLibraries)<=64 forall(aepName aepLibraries aiEntryProjectName(aepName))
          equal(buildString(aepLibraries ",") caddr(aepFields))
          equal(buildString(foreach(mapcar aepTarget aepTargets buildString(aepTarget ",")) ";")
            cadr(aepFields))) error("Invalid project observations"))
        aepData=aiProjectObserve(aepTargets aepLibraries car(aepFields)))
      ("project_receipt"
        unless(and(length(aepFields)==4 strlen(car(aepFields))>0 strlen(car(aepFields))<=128
          rexMatchp("^[A-Za-z0-9_-]+$" car(aepFields))
          strlen(caddr(aepFields))==64 rexMatchp("^[a-f0-9]+$" caddr(aepFields)))
          error("Invalid receipt identity"))
        aepPrefix=case(cadddr(aepFields)
          ("create_circuit_library" "library-create:")
          ("apply_cdf_update" "cdf-update:")
          ("create_circuit_from_plan" "create-execute:")
          ("edit_circuit" "edit-execute:")
          (("create_template_symbol" "draw_symbol") "template-symbol:")
          ("create_circuit_config" "config:")
          ("create_simulation_setup" "simulation-create:")
          ("run_simulation_setup" "simulation-run:"))
        unless(and(aepPrefix strlen(cadr(aepFields))>strlen(aepPrefix)
          strlen(cadr(aepFields))<=strlen(aepPrefix)+80
          substring(cadr(aepFields) 1 strlen(aepPrefix))==aepPrefix
          rexMatchp("^[A-Za-z0-9_-]+$" substring(cadr(aepFields) strlen(aepPrefix)+1)))
          error("Invalid receipt operation key"))
        when(and(boundp('sicoNativeIdentity) sicoNativeIdentity aiCopilotClaims[cadr(aepFields)])
          unless(equal(aiCopilotClaims[cadr(aepFields)] aiCopilotOwner(aepBinding))
            error("Operation receipt belongs to another logical session")))
        aepData=aiProjectReceipt(car(aepFields) cadr(aepFields) caddr(aepFields) cadddr(aepFields)))
      (t error("Unsupported project read")))
    sicoProjectContextJson(aepBinding)
    unless(aepBinding['projectValid] error("Task project changed during project read"))
    return(strcat("{\"valid\":true,\"project\":" aepData "}"))))
