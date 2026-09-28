unless(and(boundp('sicoGuiAskAppVersion)
             sicoGuiAskAppVersion=="20260714.recursive.safe")

procedure(GUI_askApp(flow askText "SS")
  let((ask answer)
    ask=hiDisplayAppDBox(
          ?name          'rceAskApp
          ?dboxBanner    strcat(cond(
                                  (isCallable('SICO_logo) SICO_logo())
                                  (isCallable('witMenuLogo) witMenuLogo())
                                  (t "SiCo"))
                                "::Question---" flow)
          ?dboxText      askText
          ?buttonLayout  'UserDefined
          ?buttons       '("Yes" "No")
          ?defaultButton 2
        )
    case(ask
      (1
        answer="YES"
      )
      (2
        answer="NO"
      )
    )
    answer
  );let
);procedure

sicoGuiAskAppVersion="20260714.recursive.safe"
)
