/* Owned IC231 fixture: site extensions, native menus, and frozen-window checks. */

ddCreateLib("AgentRmb" strcat(probeDirectory "/library"))
foreach(name list("origin" "other")
  cv=dbOpenCellViewByType("AgentRmb" name probeView probeType "w")
  dbSave(cv) dbClose(cv))
/* Selected cases also exercise registration before the first editor is opened. */
when(probeSelected load(probeLoader))
origin=geOpen(?lib "AgentRmb" ?cell "origin" ?view probeView ?mode "a")
other=geOpen(?lib "AgentRmb" ?cell "other" ?view probeView ?mode "r")
hiSetCurrentWindow(origin)
siteCalls=0
if(probeView=="schematic" then
  siteCommand=strcat("siteCalls=siteCalls+1 " hiGetBindKey("Schematics" "None<Btn3Down>"))
  hiSetBindKey("Schematics" "None<Btn3Down>" siteCommand)
  foreach(category list("schNone" "wire")
    hiInsertMenuItem(schGetPopUpMenu(category) hiCreateMenuItem(
      ?name 'siteRmbItem ?itemText "Site item" ?callback "t") 0))
else
  mbRegisterAction("maskLayout" "siteRmbItem" "Site item" "t" "t")
  mbSetContextData("maskLayout" "siteRmbItem" "Any None" "Canvas Navigator" "Common"))
/* Load after opening editors; load twice to cover the live-session upgrade path. */
load(probeLoader)
load(probeLoader)
hiSetCurrentWindow(origin)
if(probeSelected then
  cv=geGetWindowCellView(origin)
  object=if(probeView=="schematic" then
    car(schCreateWire(cv "draw" "direct" list(-2:0 2:0) 0.0625 0.0625 0))
  else dbCreateRect(cv list("prBoundary" "drawing") list(-2:-1 2:1)))
  unless(object error("Fixture object was not created"))
  dbSave(cv) geSelectFig(object)
  hiZoomIn(origin list(-6:-4 6:4))
else
  /* Unlike opening an already edit-open cellview with ?mode r, this is read-only. */
  geChangeEditMode("r" origin))
printf("RMB_MODE %L\n" geGetWindowCellView(origin)->mode)
when(probeView=="schematic"
  foreach(category list("schNone" "instance" "wire" "label" "schMultiple" "schPin")
    items=hiGetMenuItems(schGetPopUpMenu(category))
    unless(length(setof(item items item=='sicoSchematicAsk))==1
      error("Missing or duplicate Schematic entry: %s" category)))
  unless(hiGetBindKey("Schematics" "None<Btn3Down>")==
    strcat("sicoPrepareSchematicMenu() " siteCommand)
    error("Site binding changed or was wrapped twice")))
printf("RMB_READY\n")
probeNext=1 probeTicks=0
procedure(agentRmbPoll()
  let((file)
    probeTicks=probeTicks+1
    file=sprintf(nil "%s/command_%d.il" probeDirectory probeNext)
    when(isFile(file) probeNext=probeNext+1 load(file))
    if(or(isFile(strcat(probeDirectory "/stop")) probeTicks>600) then
      foreach(cid sicoBindings when(sicoBindings[cid] sicoStopDesktop(cid)))
      hiRegTimer("exit()" 20)
    else hiRegTimer("agentRmbPoll()" 2))))
hiRegTimer("agentRmbPoll()" 2)
