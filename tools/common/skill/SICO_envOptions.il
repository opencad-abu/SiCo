;------------------------------------------------------------------------------
; Shared helpers for name/value option lists stored in environment variables.
;------------------------------------------------------------------------------

unless(and(boundp('sicoEnvOptionsVersion)
             sicoEnvOptionsVersion=="20260924.flow.environment.v2")

procedure(SICO_parseEnvPairs(envName)
  let((env pairs item parts key val pending)
    pairs='()
    env=SICO_envValue(envName)
    when(env
      foreach(item parseString(env ";")
        when(item != ""
          parts=parseString(item ",")
          if(cdr(parts) then
            when(pending
              pairs=append1(pairs list(pending pending))
              pending=nil
            )
            key=car(parts)
            val=buildString(cdr(parts) ",")
            unless(val && val != ""
              val=key
            )
            when(key && key != ""
              pairs=append1(pairs list(key val))
            )
          else
            if(pending then
              pairs=append1(pairs list(pending item))
              pending=nil
            else
              pending=item
            )
          )
        )
      )
      when(pending
        pairs=append1(pairs list(pending pending))
      )
    )
    pairs
  )
)

procedure(SICO_envPairNames(envName)
  let((names pair)
    names='()
    foreach(pair SICO_parseEnvPairs(envName)
      names=append1(names car(pair))
    )
    if(names then names else '(""))
  )
)

procedure(SICO_envPairValue(envName selected)
  let((value pair)
    value=""
    foreach(pair SICO_parseEnvPairs(envName)
      when(selected == car(pair)
        value=cadr(pair)
      )
    )
    value
  )
)

procedure(SICO_envOptionsRevision()
  "20260924.flow.environment.v2"
)

sicoEnvOptionsVersion="20260924.flow.environment.v2"
)
