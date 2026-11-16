unless(and(boundp('sicoGuiMessageAppVersion)
             sicoGuiMessageAppVersion=="20261024.recursive.safe.v2")

procedure(GUI_messageApp(mesgType flow infoText "sSS")
  let((tmp)
    case(mesgType
      (e
        tmp=strcat("<ERROR>:\n\t" infoText)
      )
      (w
        tmp=strcat("<WARN>:\n\t" infoText)
      )
      (i
        tmp=strcat("<INFO>:\n\t" infoText)
      )
    )
    hiDisplayAppDBox(
      ?name         'rceMessageApp
      ?dboxBanner   strcat(cond(
                             (isCallable('SICO_logo) SICO_logo())
                             (isCallable('sicoMenuLogo) sicoMenuLogo())
                             (t "Silicon Copilot"))
                           "::Message---" flow)
      ?buttonLayout 'Close
      ?dboxText     tmp
    )
  );let
);procedure

sicoGuiMessageAppVersion="20261024.recursive.safe.v2"
)
