; Thin adapters: source capture remains owned by Ask, facts come from shared readers.
procedure(sicoEntryRef(aerBinding)
  unless(aerBinding['sharedWindowCaptured]
    aerBinding['sharedWindowCaptured]=t
    aerBinding['sharedWindowRef]=aiWindowCapture(aerBinding['window]))
  aerBinding['sharedWindowRef])
procedure(sicoEntryRead(aeaBinding aeaMethod aeaFields)
  prog((aeaRef aeaLimit aeaOffset aeaBasics)
    unless(length(aeaFields)==4 error("Invalid shared context arguments"))
    aeaRef=car(aeaFields)
    unless(and(strlen(aeaRef)<=96
      or(aeaRef=="-" rexMatchp("^wctx_[A-Za-z0-9_-]+_[0-9]+$" aeaRef)))
      error("Invalid shared window reference"))
    unless(and(rexMatchp("^[0-9]+$" cadr(aeaFields)) rexMatchp("^[0-9]+$" caddr(aeaFields))
      member(cadddr(aeaFields) list("0" "1"))) error("Invalid shared paging values"))
    aeaLimit=atoi(cadr(aeaFields)) aeaOffset=atoi(caddr(aeaFields)) aeaBasics=cadddr(aeaFields)=="1"
    unless(and(aeaLimit>=1 aeaLimit<=if(aeaMethod=="list_windows" 200 50)
      aeaOffset>=0 aeaOffset<=4096) error("Shared query limit"))
    when(and(aeaRef=="-" aeaMethod=="inspect_window")
      return(aiPdkError("invalid_arguments" "inspect_window requires an explicit reference")))
    when(aeaRef=="-" aeaRef=sicoEntryRef(aeaBinding))
    when(and(!aeaRef aeaMethod=="get_entry_context")
      return(aiPdkError("source_not_captured" "The task source was not captured")))
    return(case(aeaMethod
      ("list_windows" aiWindowList(aeaOffset aeaLimit))
      ("inspect_window" aiWindowInspect(aeaRef))
      ("get_entry_context"
        aiEntryContext(aeaRef aeaLimit aeaBasics
          when(aeaRef==aeaBinding['sharedWindowRef] aeaBinding['testName])
          when(aeaRef==aeaBinding['sharedWindowRef] aeaBinding['historyName])))))))
