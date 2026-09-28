;==============================================================================
; Form construction and menu entry for cadChangeTechLib.
;==============================================================================

unless(and(boundp('sicoChangeTechLibGuiVersion)
           sicoChangeTechLibGuiVersion=="20260902.form.lifecycle.v1"
           isCallable('cadChangeTechLibGuiApplyCB)
           isCallable('cadChangeTechLibGuiViaAddMappingCB)
           isCallable('cadChangeTechLibGuiViaSourceLibCB))
  cond(
    (and(isCallable('cadLoadRelative)
         cadLoadRelative("utility/skill/cadChangeTechLibGui.il"))
      t)
    (and(isCallable('witMenuLoadRelative)
         witMenuLoadRelative("utility/skill/cadChangeTechLibGui.il"))
      t)
    (and(isFile("utility/skill/cadChangeTechLibGui.il")
         errset(load("utility/skill/cadChangeTechLibGui.il") nil))
      t)
    (and(isCallable('SICO_installationRoot)
      let((guiPath)
        guiPath=strcat(SICO_installationRoot()
                       "/tools/utility/skill/cadChangeTechLibGui.il")
        when(isFile(guiPath)
          errset(load(guiPath) nil))))
      t)
    (t
      warn("cadChangeTechLib form requires SICO_HOME or sicoToolRegister.il.\n"))
  )
)

unless(and(boundp('cadChangeTechLibFormVersion)
             cadChangeTechLibFormVersion=="20260902.form.lifecycle.v1"
             isCallable('cadChangeTechLibGui))

; Do not delete an old form while loading this file.  If it is still valid,
; the GUI entry point below reuses it; a stale value is cleared explicitly.
when(and(boundp('cadChangeTechLibForm) cadChangeTechLibForm
          isCallable('hiIsForm) !hiIsForm(cadChangeTechLibForm))
  cadChangeTechLibForm=nil)

unless(boundp('cadChangeTechLibForm)
  cadChangeTechLibForm=nil
)

procedure(cadChangeTechLibGuiCreateForm()
  let((designLib designCell designView sourceLib sourceCell sourceView
        targetLib targetCell targetView mappingFile mappingList status
        loadButton saveButton addButton updateButton removeButton clearButton
        updateViaMaster viaSourceLib viaSourceDef viaTargetLib viaTargetDef
        viaMappingFile viaMappingList viaLoadButton viaSaveButton
        viaAddButton viaUpdateButton viaRemoveButton viaClearButton
        viaSourceLayout viaTargetLayout viaEditButtons viaFileLayout
        viaMappingEditorLayout viaMasterLayout viaEmptyLayout viaStackLayout
        designLayout sourceLayout targetLayout editButtons fileLayout
        mappingLayout mainLayout form cv defaultLib defaultCell defaultView
        defaultMap defaultViaMap)
    cv=if(isCallable('geGetEditCellView) geGetEditCellView() nil)
    defaultLib=if(and(cv cv~>libName) then cv~>libName else "")
    defaultCell=if(and(cv cv~>cellName) then cv~>cellName else "")
    defaultView=if(and(cv cv~>viewName) then cv~>viewName else "schematic")
    defaultMap=simplifyFilename(strcat(
      or(getShellEnvVar("PWD") ".") "/cadChangeTechLib.map") t)
    defaultViaMap=simplifyFilename(strcat(
      or(getShellEnvVar("PWD") ".") "/cadChangeTechLibVia.map") t)

    designLib=ddHiCreateLibraryComboField(
      ?name 'cadCtlDesignLib ?prompt "Design Library:"
      ?defValue defaultLib)
    designCell=ddHiCreateCellComboField(
      ?name 'cadCtlDesignCell ?prompt "Design Cell:"
      ?defValue defaultCell ?editable t)
    designView=ddHiCreateViewComboField(
      ?name 'cadCtlDesignView ?prompt "Design View:"
      ?defValue defaultView ?editable t
      ?callback "cadChangeTechLibGuiDesignViewCB(hiGetCurrentForm())")
    designLayout=hiCreateHorizontalBoxLayout('cadChangeTechLibDesignLayout
      ?frame "Design CellView" ?horiz_align 'left ?spacing 8
      ?items list(designLib designCell designView))

    sourceLib=ddHiCreateLibraryComboField(
      ?name 'cadCtlSourceLib ?prompt "Source Lib:" ?editable t)
    sourceCell=ddHiCreateCellComboField(
      ?name 'cadCtlSourceCell ?prompt "Source Cell:" ?editable t)
    sourceView=ddHiCreateViewComboField(
      ?name 'cadCtlSourceView ?prompt "Source View:" ?editable t)
    sourceLayout=hiCreateHorizontalBoxLayout('cadChangeTechLibSourceLayout
      ?frame "Source Master" ?horiz_align 'left ?spacing 8
      ?items list(sourceLib sourceCell sourceView))

    targetLib=ddHiCreateLibraryComboField(
      ?name 'cadCtlTargetLib ?prompt "Target Lib:" ?editable t)
    targetCell=ddHiCreateCellComboField(
      ?name 'cadCtlTargetCell ?prompt "Target Cell:" ?editable t)
    targetView=ddHiCreateViewComboField(
      ?name 'cadCtlTargetView ?prompt "Target View:" ?editable t)
    targetLayout=hiCreateHorizontalBoxLayout('cadChangeTechLibTargetLayout
      ?frame "Target Master" ?horiz_align 'left ?spacing 8
      ?items list(targetLib targetCell targetView))

    addButton=hiCreateButton(
      ?name 'cadCtlAddButton ?buttonText "Add"
      ?callback "cadChangeTechLibGuiAddMappingCB(hiGetCurrentForm())")
    updateButton=hiCreateButton(
      ?name 'cadCtlUpdateButton ?buttonText "Update"
      ?callback "cadChangeTechLibGuiUpdateMappingCB(hiGetCurrentForm())")
    removeButton=hiCreateButton(
      ?name 'cadCtlRemoveButton ?buttonText "Remove"
      ?callback "cadChangeTechLibGuiRemoveMappingCB(hiGetCurrentForm())")
    clearButton=hiCreateButton(
      ?name 'cadCtlClearButton ?buttonText "Clear"
      ?callback "cadChangeTechLibGuiClearMappingCB(hiGetCurrentForm())")
    editButtons=hiCreateHorizontalBoxLayout('cadChangeTechLibEditButtons
      ?horiz_align 'left ?spacing 8
      ?items list(addButton updateButton removeButton clearButton))

    mappingList=hiCreateListBoxField(
      ?name 'cadCtlMappingList ?prompt "Instance Mappings:"
      ?choices list() ?valueByPosition t ?numRows 7
      ?changeCB "cadChangeTechLibGuiSelectMappingCB(hiGetCurrentForm())")
    mappingLayout=hiCreateVerticalBoxLayout('cadChangeTechLibMappingLayout
      ?frame "Instance Master Mappings" ?horiz_align 'left ?spacing 8
      ?items list(mappingList sourceLayout targetLayout editButtons))

    mappingFile=hiCreateFileSelectorField(
      ?name 'cadCtlMappingFile ?prompt "Instance Mapping File:"
      ?mode 'anyFile ?defValue defaultMap ?editable t
      ?confirmOverwrite t)
    loadButton=hiCreateButton(
      ?name 'cadCtlLoadButton ?buttonText "Load"
      ?callback "cadChangeTechLibGuiLoadMappingCB(hiGetCurrentForm())")
    saveButton=hiCreateButton(
      ?name 'cadCtlSaveButton ?buttonText "Save"
      ?callback "cadChangeTechLibGuiSaveMappingCB(hiGetCurrentForm())")
    fileLayout=hiCreateHorizontalBoxLayout('cadChangeTechLibFileLayout
      ?horiz_align 'left ?spacing 8
      ?items list(mappingFile loadButton saveButton))

    updateViaMaster=hiCreateBooleanButton(
      ?name 'cadCtlUpdateViaMaster ?buttonText "Update Via Master"
      ?defValue nil
      ?callback "cadChangeTechLibGuiViaToggleCB(hiGetCurrentForm())")
    viaSourceLib=ddHiCreateLibraryComboField(
      ?name 'cadCtlViaSourceLib ?prompt "Via Source Lib:" ?editable t
      ?callback "cadChangeTechLibGuiViaSourceLibCB(hiGetCurrentForm())")
    viaSourceDef=hiCreateComboField(
      ?name 'cadCtlViaSourceDef ?prompt "Source ViaDef:"
      ?items list("*") ?defValue "" ?editable t)
    viaSourceLayout=hiCreateHorizontalBoxLayout(
      'cadChangeTechLibViaSourceLayout
      ?frame "Via Source Master" ?horiz_align 'left ?spacing 8
      ?items list(viaSourceLib viaSourceDef))

    viaTargetLib=ddHiCreateLibraryComboField(
      ?name 'cadCtlViaTargetLib ?prompt "Via Target Lib:" ?editable t
      ?callback "cadChangeTechLibGuiViaTargetLibCB(hiGetCurrentForm())")
    viaTargetDef=hiCreateComboField(
      ?name 'cadCtlViaTargetDef ?prompt "Target ViaDef:"
      ?items list("*") ?defValue "" ?editable t)
    viaTargetLayout=hiCreateHorizontalBoxLayout(
      'cadChangeTechLibViaTargetLayout
      ?frame "Via Target Master" ?horiz_align 'left ?spacing 8
      ?items list(viaTargetLib viaTargetDef))

    viaAddButton=hiCreateButton(
      ?name 'cadCtlViaAddButton ?buttonText "Add"
      ?callback "cadChangeTechLibGuiViaAddMappingCB(hiGetCurrentForm())")
    viaUpdateButton=hiCreateButton(
      ?name 'cadCtlViaUpdateButton ?buttonText "Update"
      ?callback "cadChangeTechLibGuiViaUpdateMappingCB(hiGetCurrentForm())")
    viaRemoveButton=hiCreateButton(
      ?name 'cadCtlViaRemoveButton ?buttonText "Remove"
      ?callback "cadChangeTechLibGuiViaRemoveMappingCB(hiGetCurrentForm())")
    viaClearButton=hiCreateButton(
      ?name 'cadCtlViaClearButton ?buttonText "Clear"
      ?callback "cadChangeTechLibGuiViaClearMappingCB(hiGetCurrentForm())")
    viaEditButtons=hiCreateHorizontalBoxLayout(
      'cadChangeTechLibViaEditButtons
      ?horiz_align 'left ?spacing 8
      ?items list(viaAddButton viaUpdateButton viaRemoveButton viaClearButton))

    viaMappingList=hiCreateListBoxField(
      ?name 'cadCtlViaMappingList ?prompt "Via Mappings:"
      ?choices list() ?valueByPosition t ?numRows 6
      ?changeCB "cadChangeTechLibGuiViaSelectMappingCB(hiGetCurrentForm())")
    viaMappingFile=hiCreateFileSelectorField(
      ?name 'cadCtlViaMappingFile ?prompt "Via Mapping File:"
      ?mode 'anyFile ?defValue defaultViaMap ?editable t
      ?confirmOverwrite t)
    viaLoadButton=hiCreateButton(
      ?name 'cadCtlViaLoadButton ?buttonText "Load"
      ?callback "cadChangeTechLibGuiViaLoadMappingCB(hiGetCurrentForm())")
    viaSaveButton=hiCreateButton(
      ?name 'cadCtlViaSaveButton ?buttonText "Save"
      ?callback "cadChangeTechLibGuiViaSaveMappingCB(hiGetCurrentForm())")
    viaFileLayout=hiCreateHorizontalBoxLayout('cadChangeTechLibViaFileLayout
      ?horiz_align 'left ?spacing 8
      ?items list(viaMappingFile viaLoadButton viaSaveButton))
    viaMappingEditorLayout=hiCreateVerticalBoxLayout(
      'cadChangeTechLibViaMappingEditorLayout
      ?horiz_align 'left ?spacing 8
      ?items list(viaMappingList viaSourceLayout viaTargetLayout
                  viaEditButtons viaFileLayout))
    viaMasterLayout=hiCreateVerticalBoxLayout('cadChangeTechLibViaMasterLayout
      ?frame "Layout Via Master Mappings" ?horiz_align 'left ?spacing 8
      ?items list(viaMappingEditorLayout))
    viaEmptyLayout=hiCreateVerticalBoxLayout(
      'cadChangeTechLibViaEmptyLayout ?items list())
    viaStackLayout=hiCreateStackedLayout('cadChangeTechLibViaStackLayout
      ?items list(viaEmptyLayout viaMasterLayout) ?currentIndex 1)

    status=hiCreateStringField(
      ?name 'cadCtlStatus ?prompt "Status:"
      ?defValue "Ready" ?editable nil)
    mainLayout=hiCreateVerticalBoxLayout('cadChangeTechLibMainLayout
      ?margins list(10 10 10 10) ?spacing 10
      ?items list(designLayout mappingLayout fileLayout updateViaMaster
                  viaStackLayout status))

    form=hiCreateLayoutForm(
      'cadChangeTechLibForm
        strcat(cadChangeTechLibBrand() "::Change Tech Library") mainLayout
      ?dialogStyle 'modeless ?sizePolicy 'expanding
      ?initialSize list(1120 920)
      ?buttonLayout list('Empty
        list('Apply 'cadChangeTechLibGuiApplyCB)
        list('Close 'cadChangeTechLibGuiCloseCB)))
    when(form
      form~>cadMappings=nil
      form~>cadSelectedIndex=nil
      form~>cadViaMappings=nil
      form~>cadViaSelectedIndex=nil
      form~>cadViaSourceChoiceLib=nil
      form~>cadViaTargetChoiceLib=nil
      hiInstantiateForm(form)
      ddHiLinkFields(form~>cadCtlDesignLib form~>cadCtlDesignCell
                     form~>cadCtlDesignView)
      ddHiLinkFields(form~>cadCtlSourceLib form~>cadCtlSourceCell
                     form~>cadCtlSourceView)
      ddHiLinkFields(form~>cadCtlTargetLib form~>cadCtlTargetCell
                     form~>cadCtlTargetView)
      form~>cadCtlViaSourceDef~>hiShowPopupCallback=
        "cadChangeTechLibGuiViaSourceLibCB(hiGetCurrentForm())"
      form~>cadCtlViaTargetDef~>hiShowPopupCallback=
        "cadChangeTechLibGuiViaTargetLibCB(hiGetCurrentForm())"
      hiSetFieldMinSize(form 'cadCtlMappingFile ?widgetWidth 500)
      hiSetFieldMinSize(form 'cadCtlMappingList
        ?widgetWidth 900 ?widgetHeight 160)
      hiSetFieldMinSize(form 'cadCtlViaMappingFile ?widgetWidth 500)
      hiSetFieldMinSize(form 'cadCtlViaMappingList
        ?widgetWidth 900 ?widgetHeight 130)
      hiSetFieldMinSize(form 'cadCtlViaSourceDef ?widgetWidth 260)
      hiSetFieldMinSize(form 'cadCtlViaTargetDef ?widgetWidth 260)
      cadChangeTechLibGuiRefreshViaDefChoices(form)
      cadChangeTechLibGuiUpdateViaControls(form)
    )
    form
  )
)

procedure(cadChangeTechLibGui()
  unless(and(boundp('cadChangeTechLibForm) cadChangeTechLibForm
             if(isCallable('hiIsForm) then hiIsForm(cadChangeTechLibForm) else t))
    cadChangeTechLibForm=cadChangeTechLibGuiCreateForm()
  )
  when(cadChangeTechLibForm
    hiDisplayForm(cadChangeTechLibForm)
  )
  cadChangeTechLibForm
)

procedure(cadDisplayCadChangeTechLibForm()
  cadChangeTechLibGui()
)

procedure(cadChangeTechLibFormRevision()
  cadChangeTechLibFormVersion
)

cadChangeTechLibFormVersion="20260902.form.lifecycle.v1"
)
