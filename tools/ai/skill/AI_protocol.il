; Restricted JSONL codec for the trusted Python session controller.

unless(and(boundp('aiProtocolVersion)
             aiProtocolVersion=="20260909.ai.protocol.v4")

procedure(aiJsonWhitespaceP(character)
  member(character list(" " "\t" "\r" "\n"))
)

procedure(aiJsonSkipWhitespace(text position)
  let((length)
    length=strlen(text)
    while(position<=length &&
          aiJsonWhitespaceP(substring(text position 1))
      position=position+1)
    position
  )
)

; Parse result: (ok value next-position diagnostic).
procedure(aiJsonParseString(text position)
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
            (t return(list(nil "" position "unsupported JSON escape")))))
        ((charToInt(concat(character))<32)
          return(list(nil "" position "unescaped JSON control character")))
        (t output=strcat(output character)))
      position=position+1)
    return(list(nil "" position "unterminated JSON string"))
  )
)

procedure(aiJsonValidateRequest(request position)
  let((id method source output)
    id=request['id]
    method=request['method]
    source=request['sourcePath]
    output=request['outputPath]
    cond(
      (not(request['hasId]) list(nil nil position "request is missing id" id))
      (or(strlen(id)<16 strlen(id)>64 !rexMatchp("^[0-9a-f][0-9a-f-]*$" id))
        list(nil nil position "request id is invalid" id))
      (not(request['hasMethod]) list(nil nil position "request is missing method" id))
      (not(member(method list("eval_skill" "eval_skill_native" "load_skill_file" "get_context"
                                  "snapshot_schematic" "capture_circuit_template")))
        list(nil nil position "request method is unsupported" id))
      (not(request['hasOutput]) list(nil nil position "request is missing output_path" id))
      (or(strlen(output)<1 strlen(output)>8192)
        list(nil nil position "output_path is invalid" id))
      (and(member(method list("eval_skill" "eval_skill_native" "load_skill_file" "snapshot_schematic"
                             "capture_circuit_template"))
            not(request['hasSource]))
        list(nil nil position "request is missing source_path" id))
      (and(request['hasSource] or(strlen(source)<1 strlen(source)>8192))
        list(nil nil position "source_path is invalid" id))
      (and(method=="get_context" request['hasSource])
        list(nil nil position "get_context must not include source_path" id))
      (t list(t request position "" id)))
  )
)

; Only a flat object with string values is accepted from the controller.
procedure(aiJsonParseRequest(text)
  prog((length position request keyResult valueResult key value character id)
    length=strlen(text)
    request=makeTable("aiJsonRequest" nil)
    position=aiJsonSkipWhitespace(text 1)
    unless(and(position<=length substring(text position 1)=="{")
      return(list(nil nil position "JSONL request must be an object" nil)))
    position=aiJsonSkipWhitespace(text position+1)
    while(position<=length
      when(substring(text position 1)=="}"
        position=aiJsonSkipWhitespace(text position+1)
        unless(position>length
          return(list(nil nil position "trailing data after request" id)))
        return(aiJsonValidateRequest(request position)))
      keyResult=aiJsonParseString(text position)
      unless(car(keyResult)
        return(list(nil nil position nth(3 keyResult) id)))
      key=cadr(keyResult)
      position=aiJsonSkipWhitespace(text nth(2 keyResult))
      unless(and(position<=length substring(text position 1)==":")
        return(list(nil nil position "expected ':' after request key" id)))
      position=aiJsonSkipWhitespace(text position+1)
      valueResult=aiJsonParseString(text position)
      unless(car(valueResult)
        return(list(nil nil position nth(3 valueResult) id)))
      value=cadr(valueResult)
      case(key
        ("id"
          when(request['hasId]
            return(list(nil nil position "duplicate id" id)))
          id=value request['id]=value request['hasId]=t)
        ("method"
          when(request['hasMethod]
            return(list(nil nil position "duplicate method" id)))
          request['method]=value request['hasMethod]=t)
        ("source_path"
          when(request['hasSource]
            return(list(nil nil position "duplicate source_path" id)))
          request['sourcePath]=value request['hasSource]=t)
        ("output_path"
          when(request['hasOutput]
            return(list(nil nil position "duplicate output_path" id)))
          request['outputPath]=value request['hasOutput]=t)
        (t return(list(nil nil position "unsupported request field" id))))
      position=aiJsonSkipWhitespace(text nth(2 valueResult))
      when(position>length
        return(list(nil nil position "unterminated request object" id)))
      character=substring(text position 1)
      cond(
        (character=="," position=aiJsonSkipWhitespace(text position+1))
        (character=="}" nil)
        (t return(list(nil nil position "expected ',' or '}'" id)))))
    return(list(nil nil position "unterminated request object" id))
  )
)

procedure(aiJsonEscape(value)
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

procedure(aiProtocolRevision()
  "20260909.ai.protocol.v4"
)

aiProtocolVersion="20260909.ai.protocol.v4"
)
