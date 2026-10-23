; Read-only geometry and connectivity record projections.
; Loaded with AI_inspect.il by the versioned AI parent.
unless(and(boundp('aiInspectVersion)
             aiInspectVersion=="20261022.ai.inspect.v11")

procedure(aiInspectPlacementJson(instance budget)
  let((protected values)
    protected=errset(list(instance~>xy instance~>orient instance~>bBox) nil)
    if(protected then
      values=car(protected)
      sprintf(nil "{\"xy\":%s,\"orient\":%s,\"bbox\":%s}"
        aiInspectPointJson(car(values))
        aiInspectJsonString(cadr(values) budget)
        aiInspectBBoxJson(caddr(values)))
    else "null")
  )
)

procedure(aiInspectInstTermJson(instTerm instanceName budget)
  let((protected name net direction dataType portKind term)
    protected=errset(instTerm~>name nil)
    name=if(protected car(protected) nil)
    protected=errset(if(instTerm~>net instTerm~>net~>name nil) nil)
    net=if(protected car(protected) nil)
    protected=errset(if(instTerm~>term instTerm~>term nil) nil)
    term=if(protected car(protected) nil)
    protected=errset(if(instTerm~>direction instTerm~>direction nil) nil)
    direction=if(protected car(protected) nil)
    when(!direction
      protected=errset(if(term term~>direction nil) nil)
      direction=if(protected car(protected) nil))
    protected=errset(if(instTerm~>dataType instTerm~>dataType nil) nil)
    dataType=if(protected car(protected) nil)
    when(!dataType
      protected=errset(if(term term~>dataType nil) nil)
      dataType=if(protected car(protected) nil))
    protected=errset(if(instTerm~>portKind instTerm~>portKind nil) nil)
    portKind=if(protected car(protected) nil)
    when(!portKind
      protected=errset(if(term term~>portKind nil) nil)
      portKind=if(protected car(protected) nil))
    sprintf(nil
      "{\"instance\":%s,\"name\":%s,\"net\":%s,\"direction\":%s,\"data_type\":%s,\"port_kind\":%s}"
      aiInspectJsonNullableString(instanceName budget)
      aiInspectJsonString(name budget)
      aiInspectJsonNullableString(net budget)
      aiInspectJsonNullableString(direction budget)
      aiInspectJsonNullableString(dataType budget)
      aiInspectJsonNullableString(portKind budget))
  )
)

procedure(aiInspectTerminalPinJson(pin budget)
  let((protected net)
    protected=errset(if(pin~>net pin~>net~>name nil) nil)
    net=if(protected car(protected) nil)
    sprintf(nil "{\"net\":%s}" aiInspectJsonNullableString(net budget))
  )
)

procedure(aiInspectSchematicInstanceJson(instance includePlacement budget)
  let((protected values placement terms remaining entry appended)
    protected=errset(list(instance~>name instance~>libName instance~>cellName
                           instance~>viewName length(instance~>instTerms)) nil)
    if(!protected then
      "{\"unavailable\":true}"
    else
      values=car(protected)
      terms=""
      remaining=instance~>instTerms
      while(remaining && !budget['full]
        entry=aiInspectInstTermJson(car(remaining) car(values) budget)
        appended=aiInspectAppendJson(terms entry budget)
        if(cadr(appended) then
          progn(terms=car(appended) remaining=cdr(remaining))
        else budget['full]=t))
      placement=if(includePlacement then
        strcat(",\"placement\":" aiInspectPlacementJson(instance budget)) else "")
      sprintf(nil
        "{\"name\":%s,\"lib\":%s,\"cell\":%s,\"view\":%s,\"term_count\":%s,\"terminals\":[%s]%s}"
        aiInspectJsonString(car(values) budget)
        aiInspectJsonString(cadr(values) budget)
        aiInspectJsonString(caddr(values) budget)
        aiInspectJsonString(cadddr(values) budget)
        aiInspectJsonNumber(car(cddddr(values))) terms placement))
  )
)

procedure(aiInspectNetJson(net budget)
  let((protected values)
    protected=errset(list(net~>name net~>numBits net~>sigType net~>isGlobal
                           length(net~>instTerms)) nil)
    if(!protected then
      "{\"unavailable\":true}"
    else
      values=car(protected)
      sprintf(nil
        "{\"name\":%s,\"bits\":%s,\"signal_type\":%s,\"global\":%s,\"instance_terminal_count\":%s}"
        aiInspectJsonString(car(values) budget)
        aiInspectJsonNumber(cadr(values))
        aiInspectJsonString(caddr(values) budget)
        aiInspectJsonBoolean(cadddr(values))
        aiInspectJsonNumber(car(cddddr(values))))
  )
)

)

procedure(aiInspectTerminalJson(terminal budget)
  let((protected values pins remaining entry appended dataType portKind)
    protected=errset(list(terminal~>name terminal~>direction terminal~>numBits
                           length(terminal~>pins)) nil)
    if(!protected then
      "{\"unavailable\":true}"
    else
      values=car(protected)
      protected=errset(terminal~>dataType nil)
      dataType=if(protected car(protected) nil)
      protected=errset(terminal~>portKind nil)
      portKind=if(protected car(protected) nil)
      pins=""
      remaining=terminal~>pins
      while(remaining && !budget['full]
        entry=aiInspectTerminalPinJson(car(remaining) budget)
        appended=aiInspectAppendJson(pins entry budget)
        if(cadr(appended) then
          progn(pins=car(appended) remaining=cdr(remaining))
        else budget['full]=t))
      sprintf(nil "{\"name\":%s,\"direction\":%s,\"bits\":%s,\"pin_count\":%s,\"data_type\":%s,\"port_kind\":%s,\"pins\":[%s]}"
        aiInspectJsonString(car(values) budget)
        aiInspectJsonString(cadr(values) budget)
        aiInspectJsonNumber(caddr(values))
        aiInspectJsonNumber(cadddr(values))
        aiInspectJsonNullableString(dataType budget)
        aiInspectJsonNullableString(portKind budget) pins))
  )
)

procedure(aiInspectLayoutShapeJson(shape budget)
  let((protected values)
    protected=errset(list(shape~>objType shape~>lpp shape~>bBox) nil)
    if(!protected then
      "{\"unavailable\":true}"
    else
      values=car(protected)
      sprintf(nil "{\"type\":%s,\"lpp\":%s,\"bbox\":%s}"
        aiInspectJsonString(car(values) budget)
        aiInspectLppJson(cadr(values) budget)
        aiInspectBBoxJson(caddr(values))))
  )
)

procedure(aiInspectLayoutInstanceJson(instance budget)
  let((protected values)
    protected=errset(list(instance~>name instance~>libName instance~>cellName
                           instance~>viewName) nil)
    if(!protected then
      "{\"unavailable\":true}"
    else
      values=car(protected)
      sprintf(nil
        "{\"name\":%s,\"lib\":%s,\"cell\":%s,\"view\":%s,\"placement\":%s}"
        aiInspectJsonString(car(values) budget)
        aiInspectJsonString(cadr(values) budget)
        aiInspectJsonString(caddr(values) budget)
        aiInspectJsonString(cadddr(values) budget)
        aiInspectPlacementJson(instance budget)))
  )
)
)
