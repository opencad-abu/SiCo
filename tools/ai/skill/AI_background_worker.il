; B1: private one-job IPC endpoint, never uses foreground sicoBindings/windows.
unless(and(boundp('aiBackgroundWorkerVersion)
           aiBackgroundWorkerVersion=="20260915.ai.background.worker.v1")

procedure(aiBackgroundLine(abwCid abwLine)
  prog((abwFields abwId abwResult abwProtected abwReply)
    abwFields=parseString(abwLine " ") abwId=car(abwFields)
    unless(and(stringp(abwId) strlen(abwId)==32 rexMatchp("^[0-9a-f]+$" abwId))
      return(nil))
    abwProtected=errset(progn(
      unless(caddr(abwFields)==aiBackgroundTarget error("Wrong worker target"))
      cond(
        (and(length(abwFields)==3 cadr(abwFields)=="get_context")
          abwResult=aiBackgroundHealth)
        (and(length(abwFields)==5 cadr(abwFields)=="assistant_call"
             nth(3 abwFields)=="eval_skill" !aiBackgroundConsumed)
          unless(nth(4 abwFields)==aiBackgroundExpectedHex error("Unexpected job payload"))
          aiBackgroundConsumed=t
          ; This expression was generated/validated by the supervisor at startup.
          ; No expression from the incoming IPC line is evaluated.
          abwResult=evalstring(aiBackgroundExpression))
        (t error("Unsupported worker request")))
      unless(stringp(abwResult) error("Worker result missing"))
      abwResult) nil)
    abwReply=if(abwProtected then sprintf(nil
      "{\"id\":\"%s\",\"ok\":true,\"result\":%s}\n" abwId car(abwProtected))
      else sprintf(nil "{\"id\":\"%s\",\"ok\":false,\"code\":\"background_unavailable\"}\n" abwId))
    unless(car(errset(ipcWriteProcess(abwCid abwReply) nil)) exit(1))
    return(t)))

procedure(aiBackgroundData(abdCid abdData)
  prog((abdPosition abdStart abdSize)
    aiBackgroundBuffer=strcat(aiBackgroundBuffer abdData)
    when(strlen(aiBackgroundBuffer)>200000 exit(1))
    abdStart=1 abdSize=strlen(aiBackgroundBuffer)
    for(abdPosition 1 abdSize
      when(substring(aiBackgroundBuffer abdPosition 1)=="\n"
        aiBackgroundLine(abdCid if(abdPosition>abdStart
          substring(aiBackgroundBuffer abdStart abdPosition-abdStart) ""))
        abdStart=abdPosition+1))
    aiBackgroundBuffer=if(abdStart<=abdSize substring(aiBackgroundBuffer abdStart) "")
    return(t)))

procedure(aiBackgroundError(abeCid abeData)
  when(abeData warn("Background relay: %s" abeData)))

procedure(aiBackgroundExit(abxCid abxStatus) exit(1))

procedure(aiBackgroundTick()
  if(isFile("stop") then exit(0)
    else hiRegTimer("aiBackgroundTick()" 2)))

procedure(aiBackgroundStart(absCommand absRegistration absSnapshot absTarget absVersion absExpression absHex)
  prog((absPid)
    unless(getVersion(t)==absVersion error("Worker Virtuoso version mismatch"))
    when(hiGraphicMode() error("Worker must be nongraphical"))
    unless(aiBackgroundProbeRevision()=="20260915.ai.background.probe.v1"
      error("Worker probe revision mismatch"))
    when(boundp('aiBackgroundCid) error("Worker already started"))
    aiBackgroundTarget=absTarget aiBackgroundExpression=absExpression
    aiBackgroundExpectedHex=absHex aiBackgroundConsumed=nil aiBackgroundBuffer=""
    absPid=ipcGetPid()
    aiBackgroundHealth=sprintf(nil "%s,\"pid\":%d,\"version\":\"%s\"}"
      substring(absSnapshot 1 strlen(absSnapshot)-1) absPid aiJsonEscape(getVersion(t)))
    aiBackgroundCid=ipcBeginProcess(absCommand "" 'aiBackgroundData 'aiBackgroundError 'aiBackgroundExit)
    unless(aiBackgroundCid error("Worker relay unavailable"))
    unless(ipcWriteProcess(aiBackgroundCid strcat(absRegistration "\n")) error("Worker registration failed"))
    hiRegTimer("aiBackgroundTick()" 2)
    return(t)))

procedure(aiBackgroundWorkerRevision() "20260915.ai.background.worker.v1")
aiBackgroundWorkerVersion="20260915.ai.background.worker.v1"
)
