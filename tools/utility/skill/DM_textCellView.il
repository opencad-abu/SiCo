; Import an existing text file into a managed Cadence text-like cellview.

unless(and(boundp('sicoTextCellViewVersion)
             sicoTextCellViewVersion=="20260715.parasitic.netlist.views")

sicoTextCellViewVersion="20260715.parasitic.netlist.views"

procedure(SICO_importTextCellView(libName cellName viewName sourceFile
                                   @optional (viewType "text"))
  prog((fileTail fileObj fileName lockId sourcePort targetPort line
         checkout result protected)
    result=nil
    unless(and(libName cellName viewName sourceFile isFile(sourceFile)
                 fileLength(sourceFile)>0)
      warn("Cannot import text cellview; source or target is invalid: %L/%L/%L <- %L\n"
            libName cellName viewName sourceFile)
      return(nil)
    )

    fileTail=ddMapGetViewTypeFileName(viewType)
    unless(fileTail
      warn("No Cadence file mapping is registered for view type %L.\n" viewType)
      return(nil)
    )

    fileObj=ddGetObj(libName cellName viewName fileTail nil "w")
    unless(fileObj
      warn("Cannot access target text cellview %s/%s/%s.\n"
            libName cellName viewName)
      return(nil)
    )

    protected=errset(
      unwindProtect(
        progn(
          checkout=car(ddAutoCheckout(list(fileObj)))
          when(checkout
            fileName=fileObj~>writePath
            lockId=ddLockPath(fileName)
            when(ddLockSet(lockId "w" nil)
              sourcePort=infile(sourceFile)
              targetPort=outfile(fileName "w")
              when(and(sourcePort targetPort)
                while(gets(line sourcePort)
                  fprintf(targetPort "%s" line)
                )
                result=t
              )
            )
          )
        )
        progn(
          when(portp(sourcePort) close(sourcePort))
          when(portp(targetPort) close(targetPort))
          when(lockId ddLockFree(lockId))
          when(fileObj ddReleaseObj(fileObj))
        )
      )
      t
    )
    unless(protected
      result=nil
    )
    unless(result
      warn("Failed to import %s into %s/%s/%s.\n"
            sourceFile libName cellName viewName)
    )
    return(result)
  )
)

)
