; Numeric and region parsing for the restricted DSPF analyzer JSONL codec.

unless(and(boundp('rceDspfRegionCodecVersion)
             rceDspfRegionCodecVersion=="20260721.dspf.regions.v1")

procedure(rceDspfJsonDigitP(character)
  member(character list("0" "1" "2" "3" "4" "5" "6" "7" "8" "9"))
)

; Parse one strict JSON number and return (ok value next-position diagnostic).
procedure(rceDspfJsonParseNumber(text position)
  prog((length start character value)
    length=strlen(text)
    start=position
    when(and(position<=length substring(text position 1)=="-")
      position=position+1)
    when(position>length
      return(list(nil nil position "expected a finite number")))
    character=substring(text position 1)
    cond(
      (character=="0" position=position+1)
      (member(character list("1" "2" "3" "4" "5" "6" "7" "8" "9"))
        position=position+1
        while(position<=length &&
              rceDspfJsonDigitP(substring(text position 1))
          position=position+1))
      (t return(list(nil nil position "expected a finite number"))))
    when(and(position<=length substring(text position 1)==".")
      position=position+1
      unless(and(position<=length
                   rceDspfJsonDigitP(substring(text position 1)))
        return(list(nil nil position "expected a digit after decimal point")))
      while(position<=length &&
            rceDspfJsonDigitP(substring(text position 1))
        position=position+1))
    when(and(position<=length
               member(substring(text position 1) list("e" "E")))
      position=position+1
      when(and(position<=length
                 member(substring(text position 1) list("+" "-")))
        position=position+1)
      unless(and(position<=length
                   rceDspfJsonDigitP(substring(text position 1)))
        return(list(nil nil position "expected a digit in number exponent")))
      while(position<=length &&
            rceDspfJsonDigitP(substring(text position 1))
        position=position+1))
    value=atof(substring(text start position-start))
    return(
      if(and(numberp(value) abs(value)<=1e15) then
        list(t value position "")
      else list(nil nil position "number is outside the finite limit")))
  )
)

procedure(rceDspfJsonParseRegions(text position)
  prog((length regions box values valueResult character count index)
    length=strlen(text)
    count=0
    position=rceDspfJsonSkipWhitespace(text position)
    unless(and(position<=length substring(text position 1)=="[")
      return(list(nil nil position "regions must be a JSON array")))
    position=rceDspfJsonSkipWhitespace(text position+1)
    when(and(position<=length substring(text position 1)=="]")
      return(list(t nil position+1 "")))
    while(position<=length
      count=count+1
      when(count>256
        return(list(nil nil position "regions exceed the 256-box limit")))
      unless(substring(text position 1)=="["
        return(list(nil nil position "region must be an array")))
      position=rceDspfJsonSkipWhitespace(text position+1)
      values=nil
      for(index 1 4
        valueResult=rceDspfJsonParseNumber(text position)
        unless(car(valueResult) return(valueResult))
        values=cons(cadr(valueResult) values)
        position=rceDspfJsonSkipWhitespace(text nth(2 valueResult))
        when(and(index<4
                   !and(position<=length substring(text position 1)==","))
          return(list(nil nil position "region must contain four numbers")))
        when(index<4
          position=rceDspfJsonSkipWhitespace(text position+1)))
      unless(and(position<=length substring(text position 1)=="]")
        return(list(nil nil position "region must contain four numbers")))
      box=reverse(values)
      when(or(car(box)>nth(2 box) cadr(box)>nth(3 box))
        return(list(nil nil position "region bounds are reversed")))
      regions=cons(box regions)
      position=rceDspfJsonSkipWhitespace(text position+1)
      when(position>length
        return(list(nil nil position "unterminated regions array")))
      character=substring(text position 1)
      cond(
        (character=="]" return(list(t reverse(regions) position+1 "")))
        (character=="," position=rceDspfJsonSkipWhitespace(text position+1))
        (t return(list(nil nil position "expected ',' or ']' in regions"))))
    )
    return(list(nil nil position "unterminated regions array"))
  )
)

procedure(rceDspfRegionCodecRevision()
  "20260721.dspf.regions.v1"
)

rceDspfRegionCodecVersion="20260721.dspf.regions.v1"
)
