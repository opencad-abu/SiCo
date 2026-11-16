; Dummy-fill form for the SiCo editor menu.

unless(boundp('SICO_dmyfillAvailableLayers)
  SICO_dmyfillAvailableLayers=nil)

unless(boundp('SICO_dmyfillLayerButtons)
  SICO_dmyfillLayerButtons=nil)

unless(boundp('SICO_dmyfillFormLayers)
  SICO_dmyfillFormLayers=nil)

unless(boundp('SICO_dmyfillTargetWindow)
  SICO_dmyfillTargetWindow=nil)

unless(boundp('SICO_dmyfillRegionMargin)
  SICO_dmyfillRegionMargin=nil)

unless(and(boundp('sicoDmyFillGuiVersion)
           sicoDmyFillGuiVersion=="20261030.sico.dmyfill.gui.v22")

unless(boundp('SICO_dmyfillRunning)
  SICO_dmyfillRunning=nil)

procedure(SICO_dmyfillGuiRevision() "20261030.sico.dmyfill.gui.v22")

procedure(SICO_dmyfillGuiScope(form)
  case(form~>dmyfillScope~>value
    ("Current Cell" "cell")
    ("Visible Window" "window")
    ("Selected Box" "selected")
    (t "cell")))

procedure(SICO_dmyfillStatus(form text)
  form~>dmyfillStatus~>value=text
  text)

procedure(SICO_dmyfillReadKeyValues(path)
  let((port line parts key value result)
    result=nil
    when(isFile(path)
      port=infile(path)
      when(port
        while(gets(line port)
          while(and(strlen(line)>0 rexMatchp("[\r\n]$" line))
            line=substring(line 1 strlen(line)-1))
          parts=parseString(line "=")
          when(parts
            key=car(parts)
            value=buildString(cdr(parts) "=")
            result=cons(list(key value) result)))
        close(port)))
    reverse(result)))

procedure(SICO_dmyfillInfoValue(info key)
  let((pair)
    pair=assoc(key info)
    when(pair cadr(pair))))

procedure(SICO_dmyfillInfoLayers(info)
  let((layers)
    layers=foreach(mapcar pair info
      when(car(pair)=="layer" cadr(pair)))
    setof(value layers value)))

procedure(SICO_dmyfillResolveInfo()
  let((path)
    path=SICO_tempPath("dmyfill-resolve.txt")
    SICO_dmyfillResolveProfile(path)
    SICO_dmyfillReadKeyValues(path)))

procedure(SICO_dmyfillResolveMessage(info)
  let((error note layers tech rule ruleName fill fillName sourceLabel)
    error=SICO_dmyfillInfoValue(info "error")
    if(error then
      sprintf(nil "Dummy fill unavailable: %s" error)
      else
      layers=SICO_dmyfillInfoLayers(info)
      note=SICO_dmyfillInfoValue(info "note")
      rule=SICO_dmyfillInfoValue(info "rule")
      fill=SICO_dmyfillInfoValue(info "fill_source")
      fillName=SICO_dmyfillInfoValue(info "fill_source_path")
      unless(fillName fillName=rule)
      ruleName=when(fillName car(last(parseString(fillName "/"))))
      sourceLabel=if(equal(fill "rule") "rule" "deck")
      tech=getShellEnvVar("PROJ_TECHLIB_NAME")
      unless(and(tech strlen(tech)>0) tech="auto")
      sprintf(nil "PDK: %s | %d metal layer(s)%s%s"
        tech length(layers)
        if(ruleName strcat(" | " sourceLabel ": " ruleName) "")
        if(and(note strlen(note)>0) strcat(" | " note) "")))))

procedure(SICO_dmyfillLayoutWindowFor(cv)
  let((current result)
    current=hiGetCurrentWindow()
    when(and(current eq(car(errset(geGetEditCellView(current) nil)) cv))
      result=current)
    unless(result
      foreach(window hiGetWindowList("maskLayout")
        unless(result
          when(eq(car(errset(geGetEditCellView(window) nil)) cv)
            result=window))))
    result))

procedure(SICO_dmyfillSelectedLayers(form)
  let((result)
    foreach(pair SICO_dmyfillLayerButtons
      when(cadr(pair)~>value
        result=append1(result car(pair))))
    result))

procedure(SICO_dmyfillBoxFieldCB(@optional value)
  let((form)
    form=if(boundp('SICO_dmyfillForm) SICO_dmyfillForm nil)
    unless(value
      when(form value=form~>dmyfillBox~>value))
    when(and(form SICO_dmyfillBoxP(value))
      SICO_dmyfillPickedBox=value
      form~>dmyfillBox~>value=value
      SICO_dmyfillStatus(form
        sprintf(nil "Box: (%g,%g) to (%g,%g)"
          nth(0 car(value)) nth(1 car(value))
          nth(0 cadr(value)) nth(1 cadr(value))))))
  t)

procedure(SICO_dmyfillBoxDone(w done pts)
  when(and(done SICO_dmyfillBoxP(pts))
    SICO_dmyfillPickedBox=pts
    when(and(boundp('SICO_dmyfillForm) SICO_dmyfillForm)
      SICO_dmyfillForm~>dmyfillBox~>value=pts
      SICO_dmyfillStatus(SICO_dmyfillForm
        sprintf(nil "Box: (%g,%g) to (%g,%g)"
          nth(0 car(pts)) nth(1 car(pts))
          nth(0 cadr(pts)) nth(1 cadr(pts))))))
  t)

procedure(SICO_dmyfillScopeCB(form)
  when(and(form form~>dmyfillBoxRow)
    form~>dmyfillBoxRow~>invisible=
      form~>dmyfillScope~>value != "Selected Box")
  t)

procedure(SICO_dmyfillPickBoxCB(form)
  let((box)
    SICO_dmyfillPickedBox=nil
    when(SICO_dmyfillTargetWindow
      hiRaiseWindow(SICO_dmyfillTargetWindow)
      hiSetCurrentWindow(SICO_dmyfillTargetWindow))
    box=enterBox(
      ?prompts list("Drag a Box.")
      ?doneProc "SICO_dmyfillBoxDone")
    when(box
      SICO_dmyfillBoxDone(hiGetCurrentWindow() t box))
    t))

procedure(SICO_dmyfillRunCB(form)
  prog((layers scope result)
    printf("[MDF] Run revision: %L %L %L\n"
      SICO_dmyfillRevision()
      SICO_dmyfillRunRevision()
      SICO_dmyfillGuiRevision())
    when(SICO_dmyfillRunning
      SICO_dmyfillStatus(form "A dummy-fill run is already in progress.")
      return(nil))
    layers=SICO_dmyfillSelectedLayers(form)
    scope=SICO_dmyfillGuiScope(form)
    when(null(layers)
      SICO_dmyfillStatus(form "Select at least one metal layer.")
      return(nil))
    when(and(scope=="selected" form~>dmyfillBox
             SICO_dmyfillBoxP(form~>dmyfillBox~>value))
      SICO_dmyfillPickedBox=form~>dmyfillBox~>value)
    when(and(scope=="selected" not(SICO_dmyfillBoxP(SICO_dmyfillPickedBox)))
      SICO_dmyfillStatus(form
        "Pick a box first, or select objects in the layout.")
      return(nil))
    SICO_dmyfillStatus(form
      "Starting GDS export and Calibre; see the Dummy Fill log window...")
    result=errset(SICO_dmyfillStart(
      buildString(layers ",") scope SICO_dmyfillTargetWindow form) nil)
    if(result then
      t
      SICO_dmyfillStatus(form
        sprintf(nil "Dummy fill failed to start: %L" errset.errset))
      nil)))

procedure(SICO_dmyfillCreateForm(layers scopeValue)
  let((cv buttons layerBox scope boxField selectButton boxRow status
       layout items form)
    cv=SICO_dmyfillCv()
    buttons=nil
    foreach(layer layers
      buttons=append1(buttons
        list(layer
          hiCreateBooleanButton(
            ?name stringToSymbol(strcat("dmyfillLayer_" layer))
            ?buttonText layer
            ?value t
            ?defValue t
            ?buttonLocation 'left))))
    SICO_dmyfillLayerButtons=buttons
    SICO_dmyfillFormLayers=layers
    layerBox=hiCreateHorizontalBoxLayout(
      'dmyfillLayerBox
      ?items foreach(mapcar pair buttons list(cadr(pair) 'stretch 0))
      ?spacing hiGetDefaultWidgetSpacing(?widgetsAreRelated t)
      ?horiz_align 'left
      ?vert_align 'center)
    scope=hiCreateRadioField(
      ?name 'dmyfillScope
      ?prompt "Region:"
      ?choices list("Current Cell" "Visible Window" "Selected Box")
      ?value if(scopeValue scopeValue "Current Cell")
      ?defValue "Current Cell"
      ?itemsPerRow 3
      ?callback list("SICO_dmyfillScopeCB(hiGetCurrentForm())"))
    boxField=hiCreateBBoxField(
      ?name 'dmyfillBox
      ?prompt "Box:"
      ?value SICO_dmyfillPickedBox
      ?editable t
      ?callback "SICO_dmyfillBoxFieldCB")
    selectButton=hiCreateButton(
      ?name 'dmyfillSelectBox
      ?buttonText "Start to select..."
      ?callback "SICO_dmyfillPickBoxCB(hiGetCurrentForm())")
    boxRow=hiCreateHorizontalBoxLayout(
      'dmyfillBoxRow
      ?items list(
        list(boxField 'stretch 1)
        list(selectButton 'stretch 0))
      ?spacing hiGetDefaultWidgetSpacing(?widgetsAreRelated t)
      ?horiz_align 'left
      ?vert_align 'center)
    status=hiCreateStringField(
      ?name 'dmyfillStatus
      ?prompt "Status:"
      ?value ""
      ?editable nil)
    items=list(
      list(layerBox 'stretch 0)
      list(scope 'stretch 0)
      list(boxRow 'stretch 0)
      list(status 'stretch 0))
    layout=hiCreateVerticalBoxLayout(
      'dmyfillLayout
      ?items items
      ?margins list(12 10 12 10)
      ?spacing hiGetDefaultWidgetSpacing(?widgetsAreRelated t)
      ?vert_align 'top)
    form=hiCreateLayoutForm(
      'SICO_dmyfillForm
      strcat(if(isCallable('SICO_logo) SICO_logo() "Silicon Copilot")
        "::MDF")
      layout
      ?buttonLayout list(
        'Empty
        list('Run "SICO_dmyfillRunCB(hiGetCurrentForm())")
        list('Close "hiFormClose(hiGetCurrentForm())"))
      ?dialogStyle 'modeless
      ?initialSize list(620 120)
      ?minSize list(620 120)
      ?sizePolicy 'expanding)
    SICO_dmyfillScopeCB(form)
    form))

procedure(SICO_dmyfillDisplayForm()
  prog((cv info layers message scopeValue)
    cv=SICO_dmyfillCv()
    unless(cv
      warn("Dummy Fill requires an editable maskLayout window.\n")
      return(nil))
    info=SICO_dmyfillResolveInfo()
    layers=SICO_dmyfillInfoLayers(info)
    message=SICO_dmyfillResolveMessage(info)
    let((margin)
      margin=SICO_dmyfillInfoValue(info "region_margin")
      SICO_dmyfillRegionMargin=when(margin atof(margin)))
    SICO_dmyfillTargetWindow=SICO_dmyfillLayoutWindowFor(cv)
    unless(SICO_dmyfillTargetWindow
      SICO_dmyfillTargetWindow=hiGetCurrentWindow())
    SICO_dmyfillAvailableLayers=layers
    when(and(boundp('SICO_dmyfillForm) SICO_dmyfillForm
             or(not(SICO_dmyfillForm~>dmyfillLayerBox)
                not(SICO_dmyfillForm~>dmyfillBox)))
      when(hiIsForm(SICO_dmyfillForm)
        hiFormDone(SICO_dmyfillForm))
      SICO_dmyfillForm=nil)
    when(and(boundp('SICO_dmyfillForm) SICO_dmyfillForm
             not(equal(SICO_dmyfillFormLayers layers)))
      scopeValue=errset(SICO_dmyfillForm~>dmyfillScope~>value nil)
      when(hiIsForm(SICO_dmyfillForm)
        hiFormDone(SICO_dmyfillForm))
      SICO_dmyfillForm=nil)
    unless(and(boundp('SICO_dmyfillForm) SICO_dmyfillForm)
      SICO_dmyfillForm=SICO_dmyfillCreateForm(layers scopeValue))
    SICO_dmyfillScopeCB(SICO_dmyfillForm)
    hiDisplayForm(SICO_dmyfillForm)
    SICO_dmyfillStatus(SICO_dmyfillForm
      sprintf(nil "Target: %s/%s/%s | %s"
        cv~>libName cv~>cellName cv~>viewName message))
    t))

sicoDmyFillGuiVersion="20261030.sico.dmyfill.gui.v22"
)
