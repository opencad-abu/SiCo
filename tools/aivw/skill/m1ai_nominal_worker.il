; Run the nominal comparator offset test against the rebound scratch DUT.

procedure(aivwM1NominalJsonString(value)
  let((text out index character)
    text=if(value then sprintf(nil "%s" value) else "")
    out="\""
    for(index 1 strlen(text)
      character=substring(text index 1)
      cond(
        (character=="\\" out=strcat(out "\\\\"))
        (character=="\"" out=strcat(out "\\\""))
        (character=="\n" out=strcat(out "\\n"))
        (t out=strcat(out character))))
    strcat(out "\"")
  )
)

let((projectDir expectedSetup expectedResults detailCsv summaryCsv evidencePath
     evidencePort session mainSetup history setupDir resultsLocation)
  printf("AIVW_M1_NOMINAL_START\n")
  projectDir=getShellEnvVar("AIVW_PROJECT_DIR")
  expectedSetup=getShellEnvVar("AIVW_SETUP_DB_DIR")
  expectedResults=getShellEnvVar("AIVW_RESULTS_LOCATION")
  detailCsv=getShellEnvVar("AIVW_DETAIL_CSV")
  summaryCsv=getShellEnvVar("AIVW_SUMMARY_CSV")
  evidencePath=getShellEnvVar("AIVW_NOMINAL_OUTPUT")
  unless(projectDir && expectedSetup && expectedResults && detailCsv &&
      summaryCsv && evidencePath
    error("one or more M1 nominal environment variables are not set"))
  unless(envSetVal("asimenv.startup" "projectDir" 'string projectDir)
    error("cannot set asimenv.startup projectDir to %L" projectDir))
  session=maeOpenSetup("saradc" "comparator_new" "maestro" ?mode "a")
  unless(session
    error("maeOpenSetup failed for scratch saradc/comparator_new/maestro"))
  mainSetup=axlGetMainSetupDB(session)
  unless(mainSetup
    maeCloseSession(?session session ?forceClose t)
    error("axlGetMainSetupDB failed for %L" session))
  setupDir=axlGetSetupDBDir(mainSetup)
  resultsLocation=axlGetResultsLocation(mainSetup)
  unless(setupDir==expectedSetup
    maeCloseSession(?session session ?forceClose t)
    error("unexpected setup DB location: %L" setupDir))
  unless(resultsLocation==expectedResults
    maeCloseSession(?session session ?forceClose t)
    error("unexpected results location: %L" resultsLocation))

  unless(maeEnableTests(?session session ?all t ?enable nil)
    maeCloseSession(?session session ?forceClose t)
    error("cannot disable all tests"))
  unless(maeEnableTests(
      ?session session
      ?testNames '("saradc:comparator_offset_TB_new:1")
      ?enable t)
    maeCloseSession(?session session ?forceClose t)
    error("cannot enable comparator offset test"))
  unless(maeSetCorner("C1" ?session session ?enabled nil)
    maeCloseSession(?session session ?forceClose t)
    error("cannot disable C1 for nominal run"))

  printf("AIVW_M1_NOMINAL_RUN\n")
  history=maeRunSimulation(?session session ?waitUntilDone t)
  unless(history
    maeCloseSession(?session session ?forceClose t)
    error("nominal offset simulation did not return a history"))
  printf("AIVW_M1_HISTORY=%L\n" history)
  unless(maeExportOutputView(
      ?session session ?fileName detailCsv ?view "Detail" ?historyName history)
    maeCloseSession(?session session ?forceClose t)
    error("cannot export nominal offset detail view"))
  unless(maeExportOutputView(
      ?session session ?fileName summaryCsv ?view "Summary" ?historyName history)
    maeCloseSession(?session session ?forceClose t)
    error("cannot export nominal offset summary view"))
  evidencePort=outfile(evidencePath "w")
  unless(evidencePort
    maeCloseSession(?session session ?forceClose t)
    error("cannot open AIVW_NOMINAL_OUTPUT: %L" evidencePath))
  fprintf(evidencePort
    "{\"schema_version\":1,\"provider\":\"Virtuoso/ADE Assembler\",\"scratch_only\":true,\"setup_db_dir\":%s,\"results_location\":%s,\"history\":%s,\"test\":\"saradc:comparator_offset_TB_new:1\",\"corner\":\"_default\"}\n"
    aivwM1NominalJsonString(setupDir)
    aivwM1NominalJsonString(resultsLocation)
    aivwM1NominalJsonString(history))
  close(evidencePort)
  unless(maeCloseSession(?session session ?forceClose t)
    error("maeCloseSession failed for %L" session))
  printf("AIVW_M1_NOMINAL_DONE\n")
)
exit(0)
