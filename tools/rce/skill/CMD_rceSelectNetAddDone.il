unless(and(boundp('rceSelectNetCommandVersion)
             rceSelectNetCommandVersion=="20260902.form.lifecycle.v1")

unless(boundp('rceSelectNetOwnerForms)
  rceSelectNetOwnerForms=makeTable("rceSelectNetOwnerForms" nil)
)

procedure(rceSelectNetOwnerSet(winid owner)
  when(and(winid
           if(isCallable('windowp) then windowp(winid) else t))
    rceSelectNetOwnerForms[winid]=
      when(and(owner
               if(isCallable('hiIsForm) then hiIsForm(owner) else t))
        owner))
  t
)

procedure(rceSelectNetOwnerGet(winid)
  when(and(winid
           if(isCallable('windowp) then windowp(winid) else t))
    rceSelectNetOwnerForms[winid])
)

procedure(rceSelectNetOwnerClear(winid)
  when(winid
    remove(winid rceSelectNetOwnerForms))
  nil
)

procedure(rceSelectNetOwnerValidP(winid)
  let((owner)
    owner=rceSelectNetOwnerGet(winid)
    and(owner
        if(isCallable('hiIsForm) then hiIsForm(owner) else t)
        owner~>netSelFile)
  )
)

procedure(CMD_rceSelectNetAdd(winid pts)
  when(and(winid pts
           if(isCallable('windowp) then windowp(winid) else t))
    geAddSelectPoint(winid nil car(last(pts))))
  t
);procedure


procedure(CMD_rceSelectNetDone(winid done pts)
  prog((netNames i netStr owner ownerValid selected protected object net)
    i=1
    netStr=""
    owner=rceSelectNetOwnerGet(winid)
    ownerValid=rceSelectNetOwnerValidP(winid)
    if(and(done winid
           if(isCallable('windowp) then windowp(winid) else t)) then
      protected=errset(geGetSelSet(winid))
      selected=when(protected car(protected))
      ; Read each selected object independently.  A deleted/stale object
      ; must not discard the remaining valid selections or abort the callback.
      foreach(object selected
        protected=errset(object~>net)
        net=when(protected car(protected))
        when(net
          protected=errset(net~>sigNames)
          netNames=when(protected car(protected))
          when(and(listp(netNames)
                   car(netNames)
                   stringp(car(netNames)))
            if(i == 1 then
              netStr=strcat(netStr car(netNames))
            else
              netStr=strcat(netStr " " car(netNames))
            )
            i++
          )
        )
      )
      GUI_messageApp('i "RCE" strcat("Selected nets are: \n\t\t" netStr "\n\tUse key \"Esc\" to cancel select."))
    else
      printf(strcat("<INFO>" SICO_logo() "::RCE: Finished select net from schematic."))
    )
    when(and(winid
             if(isCallable('windowp) then windowp(winid) else t))
      errset(hiCloseWindow(winid)))
    when(and(owner ownerValid)
      owner~>netSelFile~>value=netStr
    )
    rceSelectNetOwnerClear(winid)
  );let
);procedure

rceSelectNetCommandVersion="20260902.form.lifecycle.v1"
)
