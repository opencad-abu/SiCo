/* Bounded read-only ADE queries. Every operation uses the captured session. */

unless(and(boundp('sicoAdeVersion)
           sicoAdeVersion=="20260921.sico.ade.v1")

procedure(sicoReadText(value budget)
  let((text size remaining)
    text=cond((!value "") (stringp(value) value) (t sprintf(nil "%L" value)))
    remaining=max(0 or(budget['remaining] 40000))
    size=min(strlen(text) 512 remaining)
    when(size<strlen(text) budget['truncated]=t)
    budget['remaining]=remaining-size
    text=if(size>0 then substring(text 1 size) else "")
    sprintf(nil "\"%s\"" aiJsonEscape(text))))

procedure(sicoReadArray(items)
  strcat("[" buildString(items ",") "]"))

procedure(sicoReadLimit(items limit budget)
  let((result count)
    count=0
    foreach(item items
      if(count<limit then result=cons(item result) else budget['truncated]=t)
      count=count+1)
    reverse(result)))

procedure(sicoReadNames(items limit budget)
  let((result)
    foreach(name sicoReadLimit(items limit budget)
      result=cons(sicoReadText(name budget) result))
    sicoReadArray(reverse(result))))

procedure(sicoAdeOutputJson(output budget)
  sprintf(nil "{\"name\":%s,\"expression\":%s,\"signal\":%s}"
    sicoReadText(output->name budget)
    sicoReadText(output->expression budget)
    sicoReadText(output->signal budget)))

procedure(sicoAdeTestJson(session sdb name enabled budget)
  let((options outputs rows)
    options=axlGetTestToolArgs(axlGetTest(sdb name))
    outputs=maeGetTestOutputs(name ?session session)
    foreach(output sicoReadLimit(outputs 32 budget)
      rows=cons(sicoAdeOutputJson(output budget) rows))
    sprintf(nil
      "{\"name\":%s,\"enabled\":%s,\"testbench\":{\"lib\":%s,\"cell\":%s,\"view\":%s},\"simulator\":%s,\"output_count\":%d,\"outputs\":%s}"
      sicoReadText(name budget) if(member(name enabled) then "true" else "false")
      sicoReadText(cadr(assoc("lib" options)) budget)
      sicoReadText(cadr(assoc("cell" options)) budget)
      sicoReadText(cadr(assoc("view" options)) budget)
      sicoReadText(cadr(assoc("sim" options)) budget)
      length(outputs) sicoReadArray(reverse(rows)))))

procedure(sicoAdeSetupJson(binding)
  let((session sdb names enabled selected rows variables varRows corners budget value)
    budget=makeTable("sicoReadBudget" nil)
    session=binding['adeSession] sdb=axlGetMainSetupDB(session)
    names=cadr(axlGetTests(sdb))
    enabled=maeGetSetup(?typeName "tests" ?enabled t ?session session)
    selected=if(binding['testName] then list(binding['testName]) else names)
    foreach(name sicoReadLimit(selected 16 budget)
      rows=cons(sicoAdeTestJson(session sdb name enabled budget) rows))
    variables=cadr(axlGetVars(sdb))
    foreach(name sicoReadLimit(variables 32 budget)
      value=axlGetVarValue(axlGetVar(sdb name))
      varRows=cons(sprintf(nil "{\"name\":%s,\"value\":%s}"
        sicoReadText(name budget) sicoReadText(value budget)) varRows))
    corners=maeGetSetup(?typeName "corners" ?session session)
    value=sicoReadNames(corners 32 budget)
    sprintf(nil
      "{\"valid\":true,\"contract\":\"ade_setup.v1\",\"data_basis\":\"current_setup\",\"session\":%s,\"cellview\":%s,\"bound_test\":%s,\"bound_history\":%s,\"session_test_count\":%d,\"selected_test_count\":%d,\"tests\":%s,\"global_variable_count\":%d,\"global_variables\":%s,\"corner_count\":%d,\"corners\":%s,\"truncated\":%s,\"limits\":{\"tests\":16,\"outputs_per_test\":32,\"global_variables\":32,\"corners\":32,\"string_chars\":512,\"text_chars\":40000}}"
      sicoReadText(session budget) sicoAdeCellJson(session)
      sicoReadText(binding['testName] budget) sicoReadText(binding['historyName] budget)
      length(names) length(selected) sicoReadArray(reverse(rows))
      length(variables) sicoReadArray(reverse(varRows)) length(corners) value
      if(budget['truncated] then "true" else "false"))))

procedure(sicoAdeHistoryRow(session sdb name budget)
  let((handle result protected counts progress)
    handle=axlGetHistoryEntry(sdb name)
    unless(handle error("Captured history disappeared"))
    result=axlGetHistoryResults(handle)
    progress="null"
    protected=errset(axlGetRunStatus(session ?historyName name) nil)
    when(protected
      counts=car(protected)
      when(and(listp(counts) length(counts)==2 fixp(car(counts)) fixp(cadr(counts)))
        progress=sprintf(nil "{\"completed\":%d,\"total\":%d}" car(counts) cadr(counts))))
    sprintf(nil "{\"name\":%s,\"results_reference\":%s,\"point_counts\":%s}"
      sicoReadText(name budget) sicoReadText(result budget) progress)))

procedure(sicoAdeHistoryJson(binding)
  let((session sdb names selected rows budget)
    budget=makeTable("sicoReadBudget" nil)
    session=binding['adeSession] sdb=axlGetMainSetupDB(session)
    names=cadr(axlGetHistory(sdb))
    selected=if(binding['historyName] then list(binding['historyName]) else names)
    foreach(name sicoReadLimit(selected 32 budget)
      rows=cons(sicoAdeHistoryRow(session sdb name budget) rows))
    sprintf(nil
      "{\"valid\":true,\"contract\":\"ade_history.v1\",\"data_basis\":\"history_metadata\",\"session\":%s,\"bound_history\":%s,\"scope\":\"%s\",\"session_history_count\":%d,\"selected_history_count\":%d,\"histories\":%s,\"truncated\":%s,\"limits\":{\"histories\":32,\"string_chars\":512,\"text_chars\":40000}}"
      sicoReadText(session budget) sicoReadText(binding['historyName] budget)
      if(binding['historyName] then "history" else "ade_session")
      length(names) length(selected) sicoReadArray(reverse(rows))
      if(budget['truncated] then "true" else "false"))))

procedure(sicoAdeDispatch(binding method)
  case(method
    ("read_ade_setup" sicoAdeSetupJson(binding))
    ("read_ade_history" sicoAdeHistoryJson(binding))))

procedure(sicoAdeRevision()
  "20260921.sico.ade.v1")

sicoAdeVersion="20260921.sico.ade.v1"
)
