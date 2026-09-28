; Isolated analogLib fixture for persistent OCEAN qualification. No simulation.
procedure(mtsFixtureResistor(lib cell resistance)
  let((cv master resistor pinMaster term point farPoint pinBox check)
    when(ddGetObj(lib cell "schematic") error("fixture already exists"))
    cv=dbOpenCellViewByType(lib cell "schematic" "schematic" "a")
    master=dbOpenCellViewByType("analogLib" "res" "symbol" "" "r")
    resistor=schCreateInst(cv master "R1" 0:0 "R0")
    dbReplaceProp(resistor "r" "string" resistance)
    pinMaster=dbOpenCellViewByType("basic" "iopin" "symbol" "" "r")
    foreach(term master~>terminals
      pinBox=car(term~>pins)~>fig~>bBox
      point=dbTransformPoint(
        list((xCoord(car(pinBox))+xCoord(cadr(pinBox)))/2.0
             (yCoord(car(pinBox))+yCoord(cadr(pinBox)))/2.0)
        resistor~>transform)
      farPoint=list(xCoord(point)+1.0 yCoord(point))
      schCreateWire(cv "draw" "full" list(point farPoint) 0.0625 0.0625 0.0)
      schCreatePin(cv pinMaster term~>name "inputOutput" nil farPoint "R0")
    )
    dbClose(pinMaster)
    dbClose(master)
    check=schCheck(cv)
    unless(equal(check list(0 0)) error("fixture check failed: %L" check))
    dbSave(cv)
    dbClose(cv)
  )
)
