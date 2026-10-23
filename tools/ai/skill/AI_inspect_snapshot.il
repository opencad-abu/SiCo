; Complete schematic snapshots streamed to a private JSONL file.
; Loaded with AI_inspect.il by the versioned AI parent.
unless(and(boundp('aiInspectVersion)
             aiInspectVersion=="20261022.ai.inspect.v11")

procedure(aiInspectSnapshotHeaderJson(cv includePlacement)
  let((protected values viewType localBudget modifiedJson line)
    localBudget=aiInspectNewSnapshotBudget()
    protected=errset(list(cv~>libName cv~>cellName cv~>viewName cv~>cellViewType) nil)
    if(!protected then nil
      else
        values=car(protected)
        viewType=or(cadddr(values) caddr(values) "schematic")
        modifiedJson=aiInspectCellViewModifiedJson(cv)
        line=sprintf(nil
          "{\"record_type\":\"header\",\"schema_version\":\"cad.schematic.snapshot.v1\",\"kind\":\"schematic\",\"cellview\":{\"lib\":%s,\"cell\":%s,\"view\":%s},\"view_type\":%s,\"modified\":%s,\"hierarchy_included\":false,\"include_placement\":%s}\n"
          aiInspectJsonString(car(values) localBudget)
          aiInspectJsonString(cadr(values) localBudget)
          aiInspectJsonString(caddr(values) localBudget)
          aiInspectJsonString(viewType localBudget)
          modifiedJson
          aiInspectJsonBoolean(includePlacement))
        if(or(localBudget['full] localBudget['textTruncated]) then nil else line)))
  )

procedure(aiInspectSnapshotWriteLine(port line)
  let((offset size)
    when(and(port line stringp(line))
      offset=car(errset(fileTell(port) nil))
      size=strlen(line)
      when(and(numberp(offset) size<=4194304
               offset+size<=268435456)
        car(errset(fprintf(port "%s" line) nil))))
  )
)

procedure(aiSnapshotSchematic(lib cell view viewType outputPath includePlacement)
  let((target cv port protected remaining entry counts ok path budget closeResult
             protectedType actualType)
    target=aiInspectOpenTarget(lib cell view viewType)
    cv=target['cv]
    path=if(stringp(outputPath) outputPath "")
    counts=makeTable("aiSnapshotCounts" 0)
    protectedType=when(cv errset(cv~>cellViewType nil))
    actualType=if(protectedType then car(protectedType) else nil)
    if(!and(cv strlen(path)>0) then
      aiInspectTargetError("schematic" target 0)
    else if(actualType!="schematic" then
      progn(
        aiInspectCloseTarget(target)
        aiInspectError("schematic" "wrong_view_type"
          "snapshot_schematic requires a schematic cellview" 0))
    else
      port=outfile(path "w")
      if(!port then
        progn(
          aiInspectCloseTarget(target)
          aiInspectError("schematic" "snapshot_open_failed"
            "could not create the schematic snapshot artifact" 0))
      else
        protected=errset(progn(
          ok=aiInspectSnapshotWriteLine(port
            aiInspectSnapshotHeaderJson(cv includePlacement))
          remaining=cv~>instances
          while(and(ok remaining)
            budget=aiInspectNewSnapshotBudget()
            entry=aiInspectSchematicInstanceJson(car(remaining) includePlacement
              budget)
            if(or(budget['full] budget['textTruncated]) then
              ok=nil
            else
              progn(
                ok=aiInspectSnapshotWriteLine(port
                  sprintf(nil "{\"record_type\":\"instance\",\"data\":%s}\n" entry))
                when(ok counts['instances]=counts['instances]+1)
                remaining=cdr(remaining))))
          remaining=cv~>nets
          while(and(ok remaining)
            budget=aiInspectNewSnapshotBudget()
            entry=aiInspectNetJson(car(remaining) budget)
            if(or(budget['full] budget['textTruncated]) then
              ok=nil
            else
              progn(
                ok=aiInspectSnapshotWriteLine(port
                  sprintf(nil "{\"record_type\":\"net\",\"data\":%s}\n" entry))
                when(ok counts['nets]=counts['nets]+1)
                remaining=cdr(remaining))))
          remaining=cv~>terminals
          while(and(ok remaining)
            budget=aiInspectNewSnapshotBudget()
            entry=aiInspectTerminalJson(car(remaining) budget)
            if(or(budget['full] budget['textTruncated]) then
              ok=nil
            else
              progn(
                ok=aiInspectSnapshotWriteLine(port
                  sprintf(nil "{\"record_type\":\"terminal\",\"data\":%s}\n" entry))
                when(ok counts['terminals]=counts['terminals]+1)
                remaining=cdr(remaining))))
          when(ok
            ok=aiInspectSnapshotWriteLine(port
              sprintf(nil "{\"record_type\":\"footer\",\"counts\":{\"instances\":%d,\"nets\":%d,\"terminals\":%d},\"truncated\":false}\n"
                counts['instances] counts['nets] counts['terminals])))
          ok) nil)
        closeResult=errset(close(port) nil)
        aiInspectCloseTarget(target)
        if(and(protected car(protected) closeResult car(closeResult)) then
          sprintf(nil "{\"ok\":true,\"kind\":\"schematic_snapshot\",\"artifact_path\":%s,\"schema_version\":\"cad.schematic.snapshot.v1\"}"
            strcat("\"" aiJsonEscape(path) "\""))
        else
          progn(
            when(isFile(path) deleteFile(path))
            aiInspectError("schematic" "snapshot_write_failed"
              "could not write the complete schematic snapshot" 0)))))
  )
  )
)
)
