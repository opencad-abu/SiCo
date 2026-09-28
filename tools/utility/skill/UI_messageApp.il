unless(and(boundp('sicoGuiMessageAppVersion)
             sicoGuiMessageAppVersion=="20260714.recursive.safe")

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
                             (isCallable('witMenuLogo) witMenuLogo())
                             (t "SiCo"))
                           "::Message---" flow)
      ?buttonLayout 'Close
      ?dboxText     tmp
    )
  );let
);procedure

sicoGuiMessageAppVersion="20260714.recursive.safe"
)
