unless(and(boundp('sicoInstallationVersion)
           sicoInstallationVersion=="20261020.sico.paths.v4")

; One marked product root. No CWD lookup or ancestor search.
; The marker is canonical, single-line runtime configuration, never executable.

procedure(SICO_installationRoot(@optional launcherRoot)
  prog((configured root port line extra marker expected)
    configured=SICO_environmentValue("SICO_HOME" list("CAD_HOME"))
    unless(configured
      when(or(getShellEnvVar("SICO_ROOT") getShellEnvVar("CAD_AGENT_ROOT"))
        error("Migrate SICO_ROOT/CAD_AGENT_ROOT to an explicit SICO_HOME\n"))
      configured=launcherRoot)
    unless(and(configured strlen(configured)>0 substring(configured 1 1)=="/")
      error("SICO_HOME must name an absolute SiCo installation\n"))
    root=SICO_installationCanonicalPath(configured)
    when(and(launcherRoot root!=SICO_installationCanonicalPath(launcherRoot))
      error("SICO_HOME conflicts with the launcher installation\n"))
    marker=strcat(root "/etc/config/sico-install.json")
    unless(SICO_installationContainedP(root marker)
      error("SiCo identity escapes the installation\n"))
    port=infile(marker)
    unless(port error("Missing SiCo installation identity\n"))
    gets(line port)
    gets(extra port)
    close(port)
    expected="{\"format\":\"cad.runtime.install.v1\",\"product\":\"Silicon Copilot\",\"layout\":1}\n"
    unless(and(line==expected not(extra))
      error("Invalid SiCo installation identity\n"))
    foreach(directory list("bin" "tools" "tools/common")
      unless(and(isDir(strcat(root "/" directory))
                 SICO_installationContainedP(root strcat(root "/" directory)))
        error("Incomplete SiCo installation: %s\n" directory)))
    return(root)))

procedure(SICO_installationCanonicalPath(path)
  let((canonical)
    canonical=simplifyFilename(path)
    while(and(strlen(canonical)>1 substring(canonical strlen(canonical) 1)=="/")
      canonical=substring(canonical 1 strlen(canonical)-1))
    canonical))

procedure(SICO_installationContainedP(root path)
  let((canonical prefix)
    canonical=simplifyFilename(path)
    prefix=strcat(root "/")
    or(canonical==root
       and(strlen(canonical)>=strlen(prefix)
           substring(canonical 1 strlen(prefix))==prefix))))

procedure(SICO_installationPath(root relativePath)
  prog((result)
    unless(and(relativePath strlen(relativePath)>0
               substring(relativePath 1 1)!="/"
               not(member(".." parseString(relativePath "/")))
               not(index(relativePath "\\"))
               not(index(relativePath "\n")))
      error("Expected a relative SiCo installation path\n"))
    result=simplifyFilename(strcat(root "/" relativePath))
    unless(SICO_installationContainedP(root result)
      error("Path escapes the SiCo installation\n"))
    return(result)))

procedure(SICO_iconPath(root category filename)
  unless(and(member(category list("brand" "actions" "status"))
             filename strlen(filename)>0 not(index(filename "/")))
    error("Invalid SiCo icon name\n"))
  SICO_installationPath(root strcat("share/sico/icons/" category "/" filename)))

; Installed Python is authoritative: the bundled runtime wins over a source
; dev-runtime marker. Environment selectors are handled by the caller.
procedure(SICO_installedPython(@optional root)
  prog((bundled dev port line)
    root=or(root SICO_installationRoot())
    bundled=SICO_installationPath(root "runtime/python/bin/python3")
    when(isExecutable(bundled) return(bundled))
    dev=SICO_installationPath(root "etc/config/dev-runtime")
    when(isFile(dev)
      port=infile(dev)
      when(port
        if(gets(line port) then
          close(port)
          line=car(parseString(line "\n"))
          when(and(stringp(line) strlen(line)>0
                   substring(line 1 1)=="/" isExecutable(line))
            return(simplifyFilename(line)))
        else
          close(port))))
    nil))

; The development bootstrap is a SKILL source, so a distribution never ships it.
; Its presence is the one authority for a tree that runs tool sources; no
; compiled entry may be demanded there.
procedure(SICO_installationSourceP()
  isFile(SICO_installationPath(SICO_installationRoot() "scripts/sicoAutoLoad.il")))

sicoInstallationVersion="20261020.sico.paths.v4"
)
