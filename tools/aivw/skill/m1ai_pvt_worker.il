; Run both comparator tests at _default plus the 45-point C1 PVT matrix.

procedure(aivwM1PvtJsonString(value)
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
     maxJobsText maxJobs evidencePort session mainSetup history setupDir
     resultsLocation jobPolicy)
  printf("AIVW_M1_PVT_START\n")
  projectDir=getShellEnvVar("AIVW_PROJECT_DIR")
  expectedSetup=getShellEnvVar("AIVW_SETUP_DB_DIR")
  expectedResults=getShellEnvVar("AIVW_RESULTS_LOCATION")
  detailCsv=getShellEnvVar("AIVW_DETAIL_CSV")
  summaryCsv=getShellEnvVar("AIVW_SUMMARY_CSV")
  evidencePath=getShellEnvVar("AIVW_PVT_OUTPUT")
  maxJobsText=getShellEnvVar("AIVW_MAX_JOBS")
  unless(projectDir && expectedSetup && expectedResults && detailCsv &&
      summaryCsv && evidencePath && maxJobsText
    error("one or more M1 PVT environment variables are not set"))
  maxJobs=atoi(maxJobsText)
  unless(maxJobs>=1 && maxJobs<=8
    error("AIVW_MAX_JOBS must be between 1 and 8: %L" maxJobsText))
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
      ?testNames '("saradc:comparator_noise_TB_new:1"
                   "saradc:comparator_offset_TB_new:1")
      ?enable t)
    maeCloseSession(?session session ?forceClose t)
    error("cannot enable both comparator PVT tests"))
  unless(maeSetCorner("C1" ?session session ?enabled t)
    maeCloseSession(?session session ?forceClose t)
    error("cannot enable C1 for PVT run"))

  jobPolicy=maeGetJobPolicy(?session session ?jobType "simulation")
  unless(jobPolicy
    maeCloseSession(?session session ?forceClose t)
    error("cannot read the Maestro simulation job policy"))
  jobPolicy->maxjobs=maxJobs
  unless(maeSetJobPolicy(
      jobPolicy ?session session ?jobType "simulation")
    maeCloseSession(?session session ?forceClose t)
    error("cannot set the Maestro simulation maxjobs policy"))
  printf("AIVW_M1_PVT_MAX_JOBS=%L\n" maxJobs)

  printf("AIVW_M1_PVT_RUN\n")
  history=maeRunSimulation(?session session ?waitUntilDone t)
  unless(history
    maeCloseSession(?session session ?forceClose t)
    error("PVT simulation did not return a history"))
  printf("AIVW_M1_PVT_HISTORY=%L\n" history)
  unless(maeExportOutputView(
      ?session session ?fileName detailCsv ?view "Detail" ?historyName history)
    maeCloseSession(?session session ?forceClose t)
    error("cannot export PVT detail view"))
  unless(maeExportOutputView(
      ?session session ?fileName summaryCsv ?view "Summary" ?historyName history)
    maeCloseSession(?session session ?forceClose t)
    error("cannot export PVT summary view"))
  evidencePort=outfile(evidencePath "w")
  unless(evidencePort
    maeCloseSession(?session session ?forceClose t)
    error("cannot open AIVW_PVT_OUTPUT: %L" evidencePath))
  fprintf(evidencePort
    "{\"schema_version\":1,\"provider\":\"Virtuoso/ADE Assembler\",\"scratch_only\":true,\"setup_db_dir\":%s,\"results_location\":%s,\"history\":%s,\"tests\":[\"saradc:comparator_noise_TB_new:1\",\"saradc:comparator_offset_TB_new:1\"],\"corner_set\":\"_default+C1\",\"expected_point_count\":46,\"max_jobs\":%L}\n"
    aivwM1PvtJsonString(setupDir)
    aivwM1PvtJsonString(resultsLocation)
    aivwM1PvtJsonString(history)
    maxJobs)
  close(evidencePort)
  unless(maeCloseSession(?session session ?forceClose t)
    error("maeCloseSession failed for %L" session))
  printf("AIVW_M1_PVT_DONE\n")
)
exit(0)
