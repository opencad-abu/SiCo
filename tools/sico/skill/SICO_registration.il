unless(and(boundp('sicoRegistrationVersion)
           sicoRegistrationVersion=="20260924.sico.registration.v1")

; Publish an interactive CDNS-IC instance without opening a desktop or model.

procedure(sicoRegisterPlatform()
  prog((srpBinding)
    unless(and(isCallable('hiGraphicMode) hiGraphicMode()) return(nil))
    when(and(boundp('cadContextBuilding) cadContextBuilding) return(nil))
    srpBinding=makeTable("sicoPlatformEntry" nil)
    srpBinding['cwd]=sicoLaunchWorkingDir
    srpBinding['window]=hiGetCIWindow()
    srpBinding['entry]="platform"
    return(sicoEnsureBridge(srpBinding))))

procedure(sicoSchedulePlatformRegistration()
  when(and(isCallable('hiGraphicMode) hiGraphicMode()
           isCallable('hiRegTimer)
           !and(boundp('cadContextBuilding) cadContextBuilding))
    hiRegTimer("sicoRegisterPlatform()" 1))
  t)

sicoRegistrationVersion="20260924.sico.registration.v1"
)
