; Geometry preparation for visible-window layout export.
; Shared by AI_layout_export.il; no windows, files, or tool execution here.
unless(and(boundp('aiLayoutExportGeometryVersion)
           aiLayoutExportGeometryVersion=="20261023.ai.layout.export.geometry.v1")
procedure(aiLayoutExportGeometryRevision() "20261023.ai.layout.export.geometry.v1")
procedure(aiLayoutExportInside(altOuter altInner)
  and(altOuter altInner
    caar(altInner)>=caar(altOuter)
    caadr(altInner)<=caadr(altOuter)
    cadar(altInner)>=cadar(altOuter)
    cadadr(altInner)<=cadadr(altOuter)))

procedure(aiLayoutExportOverlap(altBox altOther)
  and(altBox altOther
    caar(altBox)<=caadr(altOther)
    caadr(altBox)>=caar(altOther)
    cadar(altBox)<=cadadr(altOther)
    cadadr(altBox)>=cadar(altOther)))

procedure(aiLayoutExportPointInside(altPoint altBox)
  and(altPoint altBox
    xCoord(altPoint)>=caar(altBox)
    xCoord(altPoint)<=caadr(altBox)
    yCoord(altPoint)>=cadar(altBox)
    yCoord(altPoint)<=cadadr(altBox)))

procedure(aiLayoutExportRound(altValue)
  if(altValue>=0 fix(altValue+0.5) fix(altValue-0.5)))

procedure(aiLayoutExportDbuBox(altBox altDbu)
  list(
    aiLayoutExportRound(caar(altBox)*altDbu)
    aiLayoutExportRound(cadar(altBox)*altDbu)
    aiLayoutExportRound(caadr(altBox)*altDbu)
    aiLayoutExportRound(cadadr(altBox)*altDbu)))

procedure(aiLayoutExportCopyable(altType)
  member(altType list("rect" "polygon" "path" "pathSeg" "line" "ellipse"
    "circle" "dot" "donut" "arc" "label")))

procedure(aiLayoutExportClippable(altType)
  member(altType list("rect" "polygon" "path" "pathSeg")))

procedure(aiLayoutExportBoxP(altBox)
  and(listp(altBox) length(altBox)==2
    listp(car(altBox)) length(car(altBox))==2
    listp(cadr(altBox)) length(cadr(altBox))==2
    numberp(caar(altBox)) numberp(cadar(altBox))
    numberp(caadr(altBox)) numberp(cadadr(altBox))))


procedure(aiLayoutExportFlatten(altCv)
  let((altOptions altCount altInst)
    altOptions=list(nil)
    altOptions->flattenPCells=t
    altOptions->flattenVias=t
    altOptions->useViaPurpose=t
    altOptions->preservePins=t
    altOptions->preservePinFigs=t
    altOptions->preserveTermName=t
    altOptions->preserveRodObjs=nil
    altOptions->preserveConn=nil
    altCount=0
    while(car(altCv~>instances)
      altInst=car(altCv~>instances)
      unless(dbFlattenInst2(altInst 32 altOptions)
        error("Visible-window instance could not be flattened"))
      altCount=altCount+1)
    while(car(altCv~>mosaics)
      altInst=car(altCv~>mosaics)
      unless(dbFlattenInst2(altInst 32 altOptions)
        error("Visible-window mosaic could not be flattened"))
      altCount=altCount+1)
    altCount))

procedure(aiLayoutExportShape(altShape altViewBox altTable)
  prog((altBox altType altLayer)
    altBox=car(errset(altShape~>bBox nil))
    unless(aiLayoutExportBoxP(altBox)
      errset(dbDeleteObject(altShape) nil)
      return(list(0 0 0 1 0)))
    altType=altShape~>objType
    cond(
      ((altType=="label")
        if(aiLayoutExportPointInside(altShape~>xy altViewBox) then
          return(list(0 0 1 0 0))
        else
          errset(dbDeleteObject(altShape) nil)
          return(list(0 0 0 1 0))))
      ((!aiLayoutExportClippable(altType))
        return(list(0 0 0 0 1)))
      ((aiLayoutExportInside(altViewBox altBox)
         return(list(0 1 0 0 0))))
      ((aiLayoutExportOverlap(altViewBox altBox)
         altLayer=list(altShape~>layerName altShape~>purpose)
         altTable[altLayer]=cons(altShape altTable[altLayer])
         return(list(1 0 0 0 0))))
      (t
        errset(dbDeleteObject(altShape) nil)
        return(list(0 0 0 1 0))))))

procedure(aiLayoutExportClip(altCv altViewBox)
  let((altTable altLayer altShapes altShape altClip altResult altPart
        altCrossing altInside altLabels altRemoved altUnclipped)
    altTable=makeTable("aiLayoutExportLayers" nil)
    altCrossing=0 altInside=0 altLabels=0 altRemoved=0 altUnclipped=0
    foreach(altShape altCv~>shapes
      altPart=aiLayoutExportShape(altShape altViewBox altTable)
      altCrossing=altCrossing+car(altPart)
      altInside=altInside+cadr(altPart)
      altLabels=altLabels+caddr(altPart)
      altRemoved=altRemoved+cadddr(altPart)
      altUnclipped=altUnclipped+nth(4 altPart))
    foreach(altLayer altTable
      altShapes=altTable[altLayer]
      altClip=car(errset(dbCreateRect(altCv altLayer altViewBox) nil))
      if(altClip then
        altResult=car(errset(dbLayerAnd(altCv altLayer altShapes
          list(altClip)) nil))
        if(altResult then
          foreach(altShape altShapes errset(dbDeleteObject(altShape) nil))
          altInside=altInside+length(altResult)
        else
          altUnclipped=altUnclipped+length(altShapes))
        errset(dbDeleteObject(altClip) nil)
      else
        altUnclipped=altUnclipped+length(altShapes)))
    list(altCrossing altInside altLabels altRemoved altUnclipped)))


aiLayoutExportGeometryVersion="20261023.ai.layout.export.geometry.v1"
)
