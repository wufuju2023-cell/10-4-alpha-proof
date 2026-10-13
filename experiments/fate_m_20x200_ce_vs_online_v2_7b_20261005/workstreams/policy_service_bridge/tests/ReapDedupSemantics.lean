import Reap.Tactic.Generator

def main : IO UInt32 := do
  let choices : List (String × Float) := [("A", -1.0), ("B", -2.0), ("A", -3.0), ("C", -4.0), ("B", -5.0)]
  let mut results : List (String × Float) := []
  for result in choices do
    results := results.insert result
  results := results.eraseDupsBy (fun x y => x.1 == y.1)
  IO.println (repr results)
  return 0
