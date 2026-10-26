# Draft: the demo video

What the rules ask of it, then a shot list that meets each ask.

* Under three minutes: aim for 2:45.
* Public on YouTube, its link in the form, in English.
* It shows the project working, and how Token Factory and an NVIDIA model are used.
* Physical AI: at least one continuous minute of the robot hardware operating or, with no
  hardware, of the key application modules in action. This entry has no hardware component, so
  the minute is both acts, unbroken.
* It shows the project on the device it was built for: the postman's onboard computer, which
  here is the GPU job on Nebius, and a computer for `explain`.
* No third-party trademarks or logos, and no music, unless there is permission for it. Name
  Agile Robots, NVIDIA and Pollen Robotics in words; show no logo.

| Time | What is on screen |
|---|---|
| 0:00 to 0:15 | One sentence: a postman robot and a household robot from different makers meet and cannot say who they are |
| 0:15 to 0:30 | The job's first lines on Nebius: an x86 server with an H100, the NVIDIA ids on Token Factory, and vLLM ready with the postman's Nemotron |
| 0:30 to 1:45 | The unbroken minute: act one, then act two, in the job's log (`nebius ai job logs --follow`), each robot's run printed in full, the postman's turns on its own Nemotron |
| 1:45 to 2:00 | The brake, in the appointment act's log: the postman's pause asked and granted, and the duck holding still |
| 2:00 to 2:20 | `explain` on the duck's passport: "Can it take a letter from my hand?", the tier first, then Nemotron's answer from the beak's limit |
| 2:20 to 2:35 | The job's last lines: vLLM's own numbers and both runs' exit codes, then the job's cost in the Nebius console |
| 2:35 to 2:45 | The repository folder and where to start |

In act one, point at the line in the duck's log saying a stranger spoke and its words were kept
from the model, and at the postman keeping the letter. In act two, at the duck's passport line
about its beak in the postman's view, the postman choosing `lay_letter_down` over
`hold_out_letter`, and the duck scooping its ball, the letter's stand-in. Say on screen that the
job is an x86 server with an H100 standing in for the postman's Jetson Thor, not a Thor, that
Agile ONE SN is a mock until it ships, that the duck's pilot is on Token Factory, and that the
duck's ball stands for the letter. The duck's animation stays inside the job's container, so show
one from a laptop run of the same act and say so. If a speed-up is needed in the unbroken
minute, say so on screen.

With the SO-101 to hand, `whois --camera` pointed at the real arm puts real hardware on camera
for a few seconds; it does not replace the minute above.
