; Shared symbol geometry contract: selection box is derived from actual pins and
; the symbol outline.  Terminal stubs and labels are deliberately excluded.
procedure(aiCircuitSymbolPinBox(acbCv)
  prog((acbBox acbX acbY acbMinX acbMinY acbMaxX acbMaxY acbSkip acbObj)
    unless(acbCv~>terminals error("selection box requires symbol terminals"))
    ; Pin figure bboxes contribute their centers and are also used to recognize
    ; the short line drawn between a terminal and the symbol body.
    foreach(acbTerm acbCv~>terminals
      unless(acbTerm~>pins error("terminal has no pin figure: %s" acbTerm~>name))
      foreach(acbPin acbTerm~>pins
        acbSkip=cons(acbPin~>fig acbSkip)
        acbBox=acbPin~>fig~>bBox
        unless(acbBox error("pin figure has no bounding box"))
        acbX=(xCoord(car(acbBox))+xCoord(cadr(acbBox)))/2.0
        acbY=(yCoord(car(acbBox))+yCoord(cadr(acbBox)))/2.0
        acbMinX=if(acbMinX min(acbMinX acbX) acbX)
        acbMinY=if(acbMinY min(acbMinY acbY) acbY)
        acbMaxX=if(acbMaxX max(acbMaxX acbX) acbX)
        acbMaxY=if(acbMaxY max(acbMaxY acbY) acbY)))
    foreach(acbObj acbCv~>shapes
      acbBox=acbObj~>bBox
      ; Pin figures, labels, and text annotations do not define the body box.
      unless(or(member(acbObj acbSkip)
                equal(acbObj~>objType "label")
                equal(acbObj~>objType "text")
                equal(acbObj~>objType "line"))
          acbMinX=if(acbMinX min(acbMinX xCoord(car(acbBox))) xCoord(car(acbBox)))
          acbMinY=if(acbMinY min(acbMinY yCoord(car(acbBox))) yCoord(car(acbBox)))
          acbMaxX=if(acbMaxX max(acbMaxX xCoord(cadr(acbBox))) xCoord(cadr(acbBox)))
          acbMaxY=if(acbMaxY max(acbMaxY yCoord(cadr(acbBox))) yCoord(cadr(acbBox)))))
    unless(and(acbMinX<acbMaxX acbMinY<acbMaxY)
      error("pin centers form a degenerate selection box; explicit layout required"))
    return(list(list(acbMinX acbMinY) list(acbMaxX acbMaxY)))))

procedure(aiCircuitCreateSymbolBox(acbCv)
  prog((acbBounds acbShape)
    acbBounds=aiCircuitSymbolPinBox(acbCv)
    acbShape=schCreateInstBox(acbCv acbBounds)
    unless(and(acbShape equal(acbShape~>bBox acbBounds))
      error("symbol selection box creation/readback failed"))
    return(acbShape)))

procedure(aiCircuitAnalogPinLabel(aclCv aclName aclPoint aclRight)
  prog((aclLabel aclPosition)
    aclPosition=list(xCoord(aclPoint)+if(aclRight 0.125 -0.125) yCoord(aclPoint)+0.125)
    aclLabel=schCreateSymbolLabel(aclCv aclPosition "analog pin annotate"
      sprintf(nil "cdsTerm(%L)" aclName) if(aclRight "lowerLeft" "lowerRight")
      "R0" "stick" 0.125 "ILLabel")
    unless(aclLabel error("analog terminal annotation failed: %s" aclName))
    return(aclLabel)))
