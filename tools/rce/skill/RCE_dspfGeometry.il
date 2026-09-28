; Exact-net OpenAccess geometry selection for DSPF resistance highlights.

unless(and(boundp('rceDspfGeometryVersion)
             rceDspfGeometryVersion=="20260721.dspf.geometry.v1")

procedure(rceDspfBBoxValues(bbox)
  prog((protected values)
    unless(bbox return(nil))
    protected=errset(
      list(xCoord(lowerLeft(bbox)) yCoord(lowerLeft(bbox))
           xCoord(upperRight(bbox)) yCoord(upperRight(bbox))) t)
    unless(protected return(nil))
    values=car(protected)
    unless(and(length(values)==4
                 numberp(car(values)) numberp(cadr(values))
                 numberp(nth(2 values)) numberp(nth(3 values)))
      return(nil))
    return(values)
  )
)

procedure(rceDspfBBoxIntersectsRegionP(bbox region)
  let((values)
    values=rceDspfBBoxValues(bbox)
    and(values
         car(values)<=nth(2 region) nth(2 values)>=car(region)
         cadr(values)<=nth(3 region) nth(3 values)>=cadr(region))
  )
)

procedure(rceDspfFigureInRegionsP(figure regions)
  prog((protected bbox region)
    protected=errset(figure~>bBox t)
    bbox=when(protected car(protected))
    foreach(region regions
      when(rceDspfBBoxIntersectsRegionP(bbox region) return(t)))
    return(nil)
  )
)

procedure(rceDspfFiguresInRegions(figures regions)
  let((selected)
    foreach(figure figures
      when(rceDspfFigureInRegionsP(figure regions)
        selected=cons(figure selected)))
    reverse(selected)
  )
)

procedure(rceDspfFigureBBox(figures)
  prog((result values)
    foreach(figure figures
      values=rceDspfBBoxValues(figure~>bBox)
      when(values
        result=if(result then
          list(min(car(result) car(values)) min(cadr(result) cadr(values))
               max(nth(2 result) nth(2 values))
               max(nth(3 result) nth(3 values)))
        else values)))
    return(
      when(result
        list(car(result):cadr(result) nth(2 result):nth(3 result))))
  )
)

procedure(rceDspfCaseCandidates(cellView name)
  let((folded names candidate candidateName)
    folded=lowerCase(name)
    foreach(candidate cellView~>nets
      when(length(names)<8
        candidateName=candidate~>name
        when(and(stringp(candidateName) candidateName!=name
                   lowerCase(candidateName)==folded)
          names=cons(candidateName names))))
    reverse(names)
  )
)

procedure(rceDspfSelectHighlightFigures(net regions hasRegions)
  let((figures selected)
    figures=net~>figs
    cond(
      (and(hasRegions regions)
        selected=rceDspfFiguresInRegions(figures regions)
        if(selected then list(selected "resistance_regions")
          else list(figures "full_net_fallback")))
      (t list(figures "full_net")))
  )
)

procedure(rceDspfCreateNetHighlight(cellView figures)
  prog((protected highlightSet added figure)
    protected=errset(
      geCreateHilightSet(cellView list("y0" "drawing") nil) t)
    highlightSet=when(protected car(protected))
    unless(highlightSet return(nil))
    added=0
    foreach(figure figures
      protected=errset(geAddHilightFig(highlightSet figure nil) t)
      when(and(protected car(protected)) added=added+1))
    unless(added>0
      errset(geDeleteHilightSet(highlightSet) t)
      return(nil))
    protected=errset(highlightSet~>enable=t t)
    unless(protected
      errset(geDeleteHilightSet(highlightSet) t)
      return(nil))
    return(list(highlightSet added))
  )
)

procedure(rceDspfGeometryRevision()
  "20260721.dspf.geometry.v1"
)

rceDspfGeometryVersion="20260721.dspf.geometry.v1"
)
