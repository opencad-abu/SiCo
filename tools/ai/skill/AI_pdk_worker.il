; Private dbAccess worker. Requests are literal data read with read(), never evaluated.
procedure(aiPdkWorkerRevision() "20260919.ai.pdk.worker.v2")

procedure(aiPdkWorkerWrite(apWritePath apWriteValue)
  let((apWritePort apWriteText)
    apWriteText=aiPdkJson(apWriteValue)
    when(strlen(apWriteText)>8388608 error("worker response limit"))
    apWritePort=outfile(strcat(apWritePath ".tmp") "w")
    unless(apWritePort error("worker output unavailable"))
    unwindProtect(fprintf(apWritePort "%s\n" apWriteText) close(apWritePort))
    unless(renameFile(strcat(apWritePath ".tmp") apWritePath) error("worker output commit failed"))
    t))

procedure(aiPdkWorker(apWorkerRoot apWorkerOwner)
  prog((apWorkerRequest apWorkerPort apWorkerRead apWorkerData apWorkerIdle apWorkerId apWorkerInit)
    apWorkerRequest=nil apWorkerPort=nil apWorkerRead=nil apWorkerData=nil
    apWorkerIdle=0 apWorkerId=nil
    aiPdkSessionRef=nil
    aiPdkWorkerWrite(strcat(apWorkerRoot "/ready.json")
      aiPdkObj(list(list("worker_revision" aiPdkWorkerRevision())
        list("context" aiPdkContext(strcat("worker:" apWorkerOwner ":" sprintf(nil "%d" ipcGetPid()))
          "background_dbAccess_worker")))))
    while(and(isDir(strcat("/proc/" apWorkerOwner))
              !isFile(strcat(apWorkerRoot "/stop")) apWorkerIdle<1200)
      if(isFile(strcat(apWorkerRoot "/request")) then
        apWorkerIdle=0
        apWorkerPort=infile(strcat(apWorkerRoot "/request"))
        unless(apWorkerPort error("worker request unavailable"))
        apWorkerRequest=unwindProtect(read(apWorkerPort) close(apWorkerPort))
        deleteFile(strcat(apWorkerRoot "/request"))
        unless(and(listp(apWorkerRequest) length(apWorkerRequest)==4
          stringp(car(apWorkerRequest)) stringp(cadr(apWorkerRequest))
          stringp(caddr(apWorkerRequest)) stringp(cadddr(apWorkerRequest)))
          error("invalid worker request"))
        apWorkerId=car(apWorkerRequest)
        apWorkerInit=nil
        apWorkerRead=errset(progn(
          apWorkerInit=aiPdkInitLibrary(cadr(apWorkerRequest))
          unless(car(apWorkerInit) error("selected PDK libInit.il failed"))
          apWorkerData=aiPdkDevice(cadr(apWorkerRequest) caddr(apWorkerRequest) cadddr(apWorkerRequest))
          aiPdkWorkerWrite(strcat(apWorkerRoot "/result.json")
            aiPdkObj(list(list("request_id" apWorkerId) list("ok" t)
              list("context" aiPdkContext(nil "background_dbAccess_worker")) list("data" apWorkerData))))
          t) t)
        unless(apWorkerRead
          aiPdkWorkerWrite(strcat(apWorkerRoot "/result.json")
            aiPdkObj(list(list("request_id" apWorkerId) list("ok" 'pdkJsonFalse)
              list("code" if(and(apWorkerInit !car(apWorkerInit))
                cadr(apWorkerInit) "capture_unavailable"))
              list("message" when(and(apWorkerInit !car(apWorkerInit)) caddr(apWorkerInit)))))))
        apWorkerData=nil apWorkerRequest=nil
      else
        ipcSleepMilli(50)
        apWorkerIdle=apWorkerIdle+1))
    return(t)))
