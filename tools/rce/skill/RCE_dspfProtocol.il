; Restricted JSONL codec for the Virtuoso DSPF analyzer bridge.

unless(and(boundp('rceDspfProtocolVersion)
             rceDspfProtocolVersion=="20260721.dspf.protocol.v3"
             isCallable('rceDspfRegionCodecRevision)
             rceDspfRegionCodecRevision()=="20260721.dspf.regions.v1")

procedure(rceDspfJsonWhitespaceP(character)
  member(character list(" " "\t" "\r" "\n"))
)

procedure(rceDspfJsonSkipWhitespace(text position)
  let((length)
    length=strlen(text)
    while(position<=length &&
          rceDspfJsonWhitespaceP(substring(text position 1))
      position=position+1
    )
    position
  )
)

; Parse results are (ok value next-position diagnostic).
procedure(rceDspfJsonParseString(text position)
  prog((length output character escape)
    length=strlen(text)
    unless(and(position<=length substring(text position 1)=="\"")
      return(list(nil "" position "expected a JSON string")))
    position=position+1
    output=""
    while(position<=length
      character=substring(text position 1)
      cond(
        (character=="\"" return(list(t output position+1 "")))
        (character=="\\"
          position=position+1
          when(position>length
            return(list(nil "" position "truncated JSON escape")))
          escape=substring(text position 1)
          case(escape
            ("\"" output=strcat(output "\""))
            ("\\" output=strcat(output "\\"))
            ("/"  output=strcat(output "/"))
            ("b"  output=strcat(output "\b"))
            ("f"  output=strcat(output "\f"))
            ("n"  output=strcat(output "\n"))
            ("r"  output=strcat(output "\r"))
            ("t"  output=strcat(output "\t"))
            (t return(list(nil "" position
              "unsupported JSON escape; \\u is not accepted")))
          )
        )
        ((charToInt(concat(character))<32)
          return(list(nil "" position "unescaped JSON control character")))
        (t output=strcat(output character))
      )
      position=position+1
    )
    return(list(nil "" position "unterminated JSON string"))
  )
)

procedure(rceDspfJsonParsePositiveInteger(text position)
  prog((length start character token value)
    length=strlen(text)
    start=position
    while(position<=length
      character=substring(text position 1)
      unless(member(character list("0" "1" "2" "3" "4" "5" "6" "7" "8" "9"))
        return(
          if(position==start then
            list(nil nil position "expected a positive integer")
          else
            token=substring(text start position-start)
            value=atoi(token)
            if(value>0 then list(t value position "")
              else list(nil nil position "id must be a positive integer")))))
      position=position+1
    )
    return(
      if(position==start then
        list(nil nil position "expected a positive integer")
      else
        token=substring(text start position-start)
        value=atoi(token)
        if(value>0 then list(t value position "")
          else list(nil nil position "id must be a positive integer"))))
  )
)

procedure(rceDspfJsonParseParams(text position)
  prog((length params keyResult key valueResult character)
    length=strlen(text)
    params=makeTable("rceDspfJsonParams" nil)
    position=rceDspfJsonSkipWhitespace(text position)
    unless(and(position<=length substring(text position 1)=="{")
      return(list(nil nil position "params must be a JSON object")))
    position=rceDspfJsonSkipWhitespace(text position+1)
    when(and(position<=length substring(text position 1)=="}")
      return(list(t params position+1 "")))
    while(position<=length
      keyResult=rceDspfJsonParseString(text position)
      unless(car(keyResult) return(keyResult))
      key=cadr(keyResult)
      position=rceDspfJsonSkipWhitespace(text nth(2 keyResult))
      unless(and(position<=length substring(text position 1)==":")
        return(list(nil nil position "expected ':' after params key")))
      position=rceDspfJsonSkipWhitespace(text position+1)
      unless(member(key list("name" "regions"))
        return(list(nil nil position "unsupported params field")))
      if(key=="name" then
        when(params['hasName]
          return(list(nil nil position "duplicate params.name field")))
        valueResult=rceDspfJsonParseString(text position)
        unless(car(valueResult) return(valueResult))
        params['name]=cadr(valueResult)
        params['hasName]=t
      else
        when(params['hasRegions]
          return(list(nil nil position "duplicate params.regions field")))
        valueResult=rceDspfJsonParseRegions(text position)
        unless(car(valueResult) return(valueResult))
        params['regions]=cadr(valueResult)
        params['hasRegions]=t)
      position=rceDspfJsonSkipWhitespace(text nth(2 valueResult))
      when(position>length
        return(list(nil nil position "unterminated params object")))
      character=substring(text position 1)
      cond(
        (character=="}" return(list(t params position+1 "")))
        (character=="," position=rceDspfJsonSkipWhitespace(text position+1))
        (t return(list(nil nil position "expected ',' or '}' in params")))
      )
    )
    return(list(nil nil position "unterminated params object"))
  )
)

; No SKILL JSON API is available, so only the frozen request object is accepted.
procedure(rceDspfJsonParseRequest(text)
  prog((length position request keyResult key valueResult character id)
    length=strlen(text)
    request=makeTable("rceDspfJsonRequest" nil)
    position=rceDspfJsonSkipWhitespace(text 1)
    unless(and(position<=length substring(text position 1)=="{")
      return(list(nil nil position "JSONL request must be an object" nil)))
    position=rceDspfJsonSkipWhitespace(text position+1)
    while(position<=length
      when(substring(text position 1)=="}"
        position=rceDspfJsonSkipWhitespace(text position+1)
        unless(position>length
          return(list(nil nil position "trailing data after JSON object" id)))
        unless(request['hasId]
          return(list(nil nil position "request is missing id" nil)))
        unless(request['hasMethod]
          return(list(nil nil position "request is missing method" id)))
        unless(request['hasParams]
          request['params]=makeTable("rceDspfJsonParams" nil))
        return(list(t request position "" id))
      )
      keyResult=rceDspfJsonParseString(text position)
      unless(car(keyResult)
        return(list(nil nil position nth(3 keyResult) id)))
      key=cadr(keyResult)
      position=rceDspfJsonSkipWhitespace(text nth(2 keyResult))
      unless(and(position<=length substring(text position 1)==":")
        return(list(nil nil position "expected ':' after request key" id)))
      position=rceDspfJsonSkipWhitespace(text position+1)
      case(key
        ("id"
          when(request['hasId]
            return(list(nil nil position "duplicate id field" id)))
          valueResult=rceDspfJsonParsePositiveInteger(text position)
          unless(car(valueResult)
            return(list(nil nil position nth(3 valueResult) id)))
          id=cadr(valueResult)
          request['id]=id
          request['hasId]=t)
        ("method"
          when(request['hasMethod]
            return(list(nil nil position "duplicate method field" id)))
          valueResult=rceDspfJsonParseString(text position)
          unless(car(valueResult)
            return(list(nil nil position nth(3 valueResult) id)))
          request['method]=cadr(valueResult)
          request['hasMethod]=t)
        ("params"
          when(request['hasParams]
            return(list(nil nil position "duplicate params field" id)))
          valueResult=rceDspfJsonParseParams(text position)
          unless(car(valueResult)
            return(list(nil nil position nth(3 valueResult) id)))
          request['params]=cadr(valueResult)
          request['hasParams]=t)
        (t return(list(nil nil position "unsupported request field" id)))
      )
      position=rceDspfJsonSkipWhitespace(text nth(2 valueResult))
      when(position>length
        return(list(nil nil position "unterminated request object" id)))
      character=substring(text position 1)
      cond(
        (character==","
          position=rceDspfJsonSkipWhitespace(text position+1)
          when(and(position<=length substring(text position 1)=="}")
            return(list(nil nil position
              "trailing comma in request object" id))))
        (character=="}" nil)
        (t return(list(nil nil position "expected ',' or '}' in request" id))))
    )
    return(list(nil nil position "unterminated request object" id))
  )
)

procedure(rceDspfJsonEscape(value)
  let((text output index character code)
    text=if(stringp(value) then value else sprintf(nil "%L" value))
    output=""
    for(index 1 strlen(text)
      character=substring(text index 1)
      code=charToInt(concat(character))
      output=strcat(output
        case(character
          ("\\" "\\\\")
          ("\"" "\\\"")
          ("\b" "\\b")
          ("\f" "\\f")
          ("\n" "\\n")
          ("\r" "\\r")
          ("\t" "\\t")
          (t if(code<32 then sprintf(nil "\\u%04x" code) else character)))))
    output
  )
)

procedure(rceDspfSendJson(cid payload)
  unless(ipcWriteProcess(cid strcat(payload "\n"))
    warn("RCE DSPF analyzer bridge could not write to child %L.\n" cid))
)

procedure(rceDspfSendError(cid id code message)
  if(and(integerp(id) id>0) then
    rceDspfSendJson(cid
      sprintf(nil "{\"id\":%d,\"ok\":false,\"error\":{\"code\":\"%s\",\"message\":\"%s\"}}"
        id rceDspfJsonEscape(code) rceDspfJsonEscape(message)))
  else
    warn("RCE DSPF analyzer rejected protocol input: %s.\n" message))
)

procedure(rceDspfSendName(cid id name)
  rceDspfSendJson(cid
    sprintf(nil "{\"id\":%d,\"ok\":true,\"result\":{\"name\":\"%s\"}}"
      id rceDspfJsonEscape(name)))
)

procedure(rceDspfSendHighlightResult(cid id name figureCount scope)
  rceDspfSendJson(cid
    sprintf(nil
      "{\"id\":%d,\"ok\":true,\"result\":{\"name\":\"%s\",\"figure_count\":%d,\"scope\":\"%s\"}}"
      id rceDspfJsonEscape(name) figureCount rceDspfJsonEscape(scope)))
)

procedure(rceDspfProtocolRevision()
  "20260721.dspf.protocol.v3"
)

rceDspfProtocolVersion="20260721.dspf.protocol.v3"
)
