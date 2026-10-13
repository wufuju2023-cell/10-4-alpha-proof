import OpenAIClient

open Lean

def main (args : List String) : IO UInt32 := do
  let some path := args.head?
    | IO.eprintln "usage: ReapResponseCompat <response.json>"; return 2
  let raw ← IO.FS.readFile path
  match Json.parse raw with
  | .error message => IO.eprintln s!"JSON error: {message}"; return 3
  | .ok value =>
    match (fromJson? value : Except String OpenAIChatResponse) with
    | .error message => IO.eprintln s!"OpenAIChatResponse error: {message}"; return 4
    | .ok response =>
      if response.choices.length != 2 then
        IO.eprintln "wrong choice count"; return 5
      let counts := response.choices.map fun choice =>
        choice.logprobs.bind (fun item => item.content) |>.map List.length |>.getD 0
      if counts != [2, 2] then
        IO.eprintln s!"wrong logprob counts: {counts}"; return 6
      IO.println "REAP_OPENAI_RESPONSE_COMPAT_PASS choices=2 token_logprobs=2,2"
      return 0
