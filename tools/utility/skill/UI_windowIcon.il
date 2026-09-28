unless(and(boundp('sicoWindowIconVersion)
             sicoWindowIconVersion=="20260921.sico.flow.icon.v1")

unless(boundp('cadWindowIconCache)
  cadWindowIconCache=nil
)

unless(boundp('cadWindowIconCachePath)
  cadWindowIconCachePath=""
)

unless(boundp('cadWindowIconWarnedPath)
  cadWindowIconWarnedPath=""
)

procedure(SICO_windowIconRevision()
  "20260921.sico.flow.icon.v1"
)

procedure(SICO_windowIconBrand()
  cond(
    (isCallable('SICO_logo) SICO_logo())
    (isCallable('witMenuLogo) witMenuLogo())
    (t "SiCo")
  )
)

procedure(SICO_windowIconPath()
  let((path)
    path=SICO_iconPath(SICO_installationRoot() "brand" "logo.png")
    when(isFile(path) path)))


procedure(SICO_windowIcon()
  let((path)
    path=SICO_windowIconPath()
    cond(
      (not(path)
        unless(cadWindowIconWarnedPath=="<missing>"
          cadWindowIconWarnedPath="<missing>"
          warn("%s CAD flow-log icon is unavailable: $SICO_HOME/share/sico/icons/brand/logo.png\n"
               SICO_windowIconBrand())
        )
        nil
      )
      (and(cadWindowIconCache
            cadWindowIconCachePath==path
            hiIsIcon(cadWindowIconCache))
        cadWindowIconCache
      )
      (t
        cadWindowIconCachePath=path
        cadWindowIconCache=hiLoadIconFile(path 48 48)
        unless(cadWindowIconCache
          unless(cadWindowIconWarnedPath==path
            cadWindowIconWarnedPath=path
            warn("%s CAD cannot load flow-log icon: %s\n"
                 SICO_windowIconBrand() path)
          )
        )
        cadWindowIconCache
      )
    )
  )
)

procedure(SICO_setWindowIcon(window)
  let((icon)
    icon=SICO_windowIcon()
    and(window windowp(window) icon hiSetWindowIcon(window icon))
  )
)

sicoWindowIconVersion="20260921.sico.flow.icon.v1"
)
