; Metadata only. Python creates the private file before configuring this writer.
unless(and(boundp('sicoDiagnosticsVersion)
           sicoDiagnosticsVersion=="20260921.sico.diagnostics.v1")

procedure(sicoDiagnostic(adgBinding adgEvent adgId adgMethod adgTarget adgCode adgCount adgLength)
  prog((adgPath adgPort adgText adgProtected)
    unless(adgBinding return(nil))
    adgPath=adgBinding['diagnosticPath]
    unless(and(adgPath isFile(adgPath)) return(nil))
    adgProtected=errset(progn(
      adgBinding['diagnosticSequence]=1+or(adgBinding['diagnosticSequence] 0)
      adgText=sprintf(nil
        "{\"protocol\":\"cad_ai_skill_stage.v1\",\"event\":\"%s\",\"request_id\":\"%s\",\"method\":\"%s\",\"target_id\":\"%s\",\"code\":\"%s\",\"field_count\":%d,\"payload_length\":%d,\"sequence\":%d,\"instance_id\":\"%s\",\"generation\":\"%s\",\"bridge_id\":\"%s\",\"router_id\":\"%s\"}\n"
        aiJsonEscape(adgEvent) aiJsonEscape(adgId) aiJsonEscape(adgMethod)
        aiJsonEscape(adgTarget) aiJsonEscape(or(adgCode "")) adgCount adgLength
        adgBinding['diagnosticSequence]
        aiJsonEscape(or(adgBinding['instanceId] "")) aiJsonEscape(or(adgBinding['generation] ""))
        aiJsonEscape(or(adgBinding['bridgeId] "")) aiJsonEscape(or(adgBinding['routerId] "")))
      when(strlen(adgText)<=8192
        adgPort=outfile(adgPath "a")
        when(adgPort
          unwindProtect(progn(fprintf(adgPort "%s" adgText) drain(adgPort)) close(adgPort))))) nil)
    unless(and(adgProtected car(adgProtected))
      unless(adgBinding['diagnosticWarning]
        adgBinding['diagnosticWarning]=t
        warn("Silicon Copilot: SKILL stage log unavailable; execution outcome is not confirmed.\n")))
    return(and(adgProtected car(adgProtected)))))

procedure(sicoConfigureDiagnostics(acdCid acdLine)
  prog((acdBinding acdFields acdPath)
    acdBinding=sicoBindings[acdCid] acdFields=parseString(acdLine " ")
    unless(and(acdBinding length(acdFields)==4 !acdBinding['diagnosticPath]) return(nil))
    unless(and(strlen(caddr(acdFields))==32 rexMatchp("^[0-9a-f]+$" caddr(acdFields))
      strlen(nth(3 acdFields))==32 rexMatchp("^[0-9a-f]+$" nth(3 acdFields))) return(nil))
    acdPath=car(errset(aiCopilotHex(cadr(acdFields)) nil))
    unless(and(stringp(acdPath) substring(acdPath 1 1)=="/" isFile(acdPath)) return(nil))
    acdBinding['diagnosticPath]=acdPath
    acdBinding['bridgeId]=caddr(acdFields) acdBinding['routerId]=nth(3 acdFields)
    return(t)))

procedure(sicoDiagnosticsRevision() "20260921.sico.diagnostics.v1")
sicoDiagnosticsVersion="20260921.sico.diagnostics.v1"
)
