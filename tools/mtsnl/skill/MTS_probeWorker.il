procedure(mtsRuntimeDefaultsAsi(sim project result cdsLibrary library cell view startup simrc outputPath
    maeLibrary maeCell maeView maeTest maeApplication maeHistory reportSuffix)

envSetVal("asimenv.startup" "projectDir" 'string project)
simulator(stringToSymbol(sim))
let((tool session baseline afterDesign afterStartup report sourceInfo outFile maeOpenAnswer maeOpenError maeSession
     maeApiErrors maeDiagnostics reportStatus maeBodyAnswer maeBodyError maeCloseDiagnostic
     maeCleanupAnswer maeCleanupError)
  tool=asiGetTool(stringToSymbol(sim))
  session=nil
  baseline=if(and(boundp('mtsProjectBaselineTable) mtsProjectBaselineTable) then
    mtsProjectBaseline(stringToSymbol(sim)) else mtsDefaultsSnapshot(tool session))
  design(library cell view "r")
  resultsDir(result)
  ddsRefresh(?cellview t ?cdf t)
  session=mtsDefaultsSafeCurrentSession()
  afterDesign=mtsDefaultsSnapshot(tool session)
  when(startup load(startup))
  when(simrc load(simrc))
  session=mtsDefaultsSafeCurrentSession()
  afterStartup=mtsDefaultsSnapshot(tool session)
  reportStatus=if(maeApiErrors then "failed" else "succeeded")
  sourceInfo=list(
    list("cds_lib" cdsLibrary)
    list("library" library)
    list("cell" cell)
    list("view" view))
  report=strcat("{\"schema_version\":1,\"status\":",
    mtsDefaultsJsonString(reportStatus), ",\"provider\":",
    mtsDefaultsJsonString("asi_initialization"), ",\"dialect\":",
    mtsDefaultsJsonString(sim), ",\"tool_name\":",
    mtsDefaultsJsonString(sim), ",\"source\":",
    mtsDefaultsJsonObject(sourceInfo), ",\"baseline\":", baseline,
    ",\"after_design\":", afterDesign,
    ",\"after_startup_simrc\":", afterStartup,
    ",\"diagnostics\":" mtsDefaultsJsonArrayValue(maeDiagnostics)
    ",\"api_errors\":" mtsDefaultsJsonArrayValue(maeApiErrors)
    reportSuffix)
  outFile=outfile(or(outputPath getShellEnvVar("MTS_NETLISTOR_DEFAULTS_OUTPUT")) "w")
  when(outFile
    fprintf(outFile "%s" report)
    close(outFile)
  )

)

t)

procedure(mtsRuntimeDefaultsMae(sim project result cdsLibrary library cell view startup simrc outputPath
    maeLibrary maeCell maeView maeTest maeApplication maeHistory reportSuffix)

envSetVal("asimenv.startup" "projectDir" 'string project)
simulator(stringToSymbol(sim))
let((tool session baseline afterDesign afterStartup report sourceInfo outFile maeOpenAnswer maeOpenError maeSession
     maeApiErrors maeDiagnostics reportStatus maeBodyAnswer maeBodyError maeCloseDiagnostic
     maeCleanupAnswer maeCleanupError)
  tool=nil
  baseline="{\"model_files\":[],\"environment_options\":{},\"simulator_options\":{}}"
  design(library cell view "r")
  resultsDir(result)
  ddsRefresh(?cellview t ?cdf t)
  maeApiErrors=nil
  maeDiagnostics=nil
  afterDesign="{\"model_files\":[],\"environment_options\":{},\"simulator_options\":{}}"
  afterStartup="{\"model_files\":[],\"environment_options\":{},\"simulator_options\":{}}"
  maeOpenAnswer=errset(apply('maeOpenSetup append(list(maeLibrary maeCell maeView)
          append(when(maeApplication list(?application maeApplication))
            append(when(maeHistory list(?histName maeHistory)) list(?mode "r"))))) nil), maeOpenError=errset.errset
  maeSession=if(maeOpenAnswer then car(maeOpenAnswer) else nil)
  unless(maeOpenAnswer
    maeApiErrors=cons(strcat("maeOpenSetup raised an exception: "
                            sprintf(nil "%L" maeOpenError)) maeApiErrors)
  )
  when(and(maeOpenAnswer null(maeSession))
    maeApiErrors=cons("maeOpenSetup returned nil (setup does not exist or could not be opened)" maeApiErrors)
  )
  if(maeSession then
    progn(
      unwindProtect(
        progn(
          maeBodyAnswer=errset(
          progn(
            afterDesign=mtsDefaultsMaeSnapshot(maeSession maeTest)
            maeApiErrors=append(maeApiErrors cadr(afterDesign))
            maeDiagnostics=append(maeDiagnostics caddr(afterDesign))
            afterDesign=car(afterDesign)
            when(startup load(startup))
            when(simrc load(simrc))
            afterStartup=mtsDefaultsMaeSnapshot(maeSession maeTest)
            maeApiErrors=append(maeApiErrors cadr(afterStartup))
            maeDiagnostics=append(maeDiagnostics caddr(afterStartup))
            afterStartup=car(afterStartup)
          )
            nil
          ), maeBodyError=errset.errset
        )
          progn(
            maeCleanupAnswer=errset(
              progn(
                maeCloseDiagnostic=mtsDefaultsMaeClose(maeSession)
                when(maeCloseDiagnostic
                  maeDiagnostics=cons(maeCloseDiagnostic maeDiagnostics)
                )
              )
              nil
            ), maeCleanupError=errset.errset
            unless(maeCleanupAnswer
              maeDiagnostics=cons(strcat("MAE cleanup raised an exception: "
                                         sprintf(nil "%L" maeCleanupError))
                                 maeDiagnostics)
            )
          )
      )
      unless(maeBodyAnswer
        maeApiErrors=append(maeApiErrors
          list(strcat("MAE defaults body raised an exception: "
                      sprintf(nil "%L" maeBodyError))))
      )
    )
  else
    nil
  )
  reportStatus=if(maeApiErrors then "failed" else "succeeded")
  sourceInfo=list(
    list("cds_lib" cdsLibrary)
    list("library" library)
    list("cell" cell)
    list("view" view))
  report=strcat("{\"schema_version\":1,\"status\":",
    mtsDefaultsJsonString(reportStatus), ",\"provider\":",
    mtsDefaultsJsonString("mae_test"), ",\"dialect\":",
    mtsDefaultsJsonString(sim), ",\"tool_name\":",
    mtsDefaultsJsonString(sim), ",\"source\":",
    mtsDefaultsJsonObject(sourceInfo), ",\"baseline\":", baseline,
    ",\"after_design\":", afterDesign,
    ",\"after_startup_simrc\":", afterStartup,
    ",\"diagnostics\":" mtsDefaultsJsonArrayValue(maeDiagnostics)
    ",\"api_errors\":" mtsDefaultsJsonArrayValue(maeApiErrors)
    reportSuffix)
  outFile=outfile(or(outputPath getShellEnvVar("MTS_NETLISTOR_DEFAULTS_OUTPUT")) "w")
  when(outFile
    fprintf(outFile "%s" report)
    close(outFile)
  )

)

t)
