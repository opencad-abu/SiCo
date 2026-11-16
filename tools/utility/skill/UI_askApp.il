unless(and(boundp('sicoGuiAskAppVersion)
             sicoGuiAskAppVersion=="20261024.recursive.safe.v2")

procedure(GUI_askApp(flow askText "SS")
  let((ask answer)
    ask=hiDisplayAppDBox(
          ?name          'rceAskApp
          ?dboxBanner    strcat(cond(
                                  (isCallable('SICO_logo) SICO_logo())
                                  (isCallable('sicoMenuLogo) sicoMenuLogo())
                                  (t "Silicon Copilot"))
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

sicoGuiAskAppVersion="20261024.recursive.safe.v2"
)
