# Simplified Technical English in the Railhead documents

## Statement

These Railhead documents follow ASD-STE100 Simplified Technical English (STE), Issue 9:

- `README.md`
- `docs/USAGE.md`
- `docs/ARCHITECTURE.md`
- `docs/TASKS.md`
- `docs/VERIFICATION.md`
- `docs/STE.md`

The documents `docs/DESIGN.md` and `docs/research/*.md` do not follow STE.
They are design notes and research notes.

## Rules that the documents apply

| Rule area | How the documents apply the rule |
|---|---|
| Words | Use approved words in their approved meanings. Use the technical names and technical verbs in this document. |
| Noun clusters | Do not use more than three nouns in a cluster. A technical name is the only exception. |
| Verbs | Use the active voice. Use the present tense when possible. |
| Instructions | Use the imperative. Write one instruction in each sentence. |
| -ing forms | Do not use -ing forms as nouns or adjectives. A technical name is the only exception. |
| Procedural sentences | Do not write more than 20 words in a sentence. |
| Descriptive sentences | Do not write more than 25 words in a sentence. |
| Paragraphs | Do not write more than six sentences in a paragraph. Each paragraph has one topic. |
| Lists | Use vertical lists for steps and for items in a sequence. |
| Conditions | Write the condition first, then the instruction. |
| Safety | Write a CAUTION before the step that it applies to. Write the instruction first, then the reason. |
| Articles | Keep "a", "an", "the", and "that" where they make the text clear. |
| Punctuation | Do not use semicolons. Do not use contractions. |

## Text that is not STE

The documents keep some text exactly as it is in the code or in the product.
This text is not subject to the STE rules:

- Code blocks and shell commands.
- Command names, file names, directory names, and environment variables.
- JSON keys, argument names, error codes, and values in code font.
- Formulas.
- Names of products and external projects, for example OpenTTD, Harbor, Docker, colima, Parquet, and Hugging Face.

A table cell that contains only a name or a value is not a sentence.

## Technical names

STE lets a writer use technical names that are specific to a project.
These documents use the technical names in the table below.
Use each technical name only with the description in this table.

### Railhead and reinforcement learning

| Technical name | Description |
|---|---|
| abstain record | The `meta` record that the command `rh abstain` writes. |
| action, player action | A player command that changes the world, for example `road.line`. |
| action log | The JSONL file that records each action, wait, and meta decision of the agent. |
| action registry | The list of all player actions in `src/railhead/actions.py`. |
| agent | The program that plays a task. Usually, an agent uses a language model. |
| attack | An action that an agent can use to receive a reward without correct play. |
| baseline | The scripted player that sets the reference value in a `tycoon` task. |
| branch point | A savegame from which a fork or a new task can start. |
| briefing | The text that tells the agent the objective of a task. |
| budget, fork-day budget | The maximum number of game days that all forks of one task can run. |
| certificate | A value that the generator calculates to show that a task is solvable. |
| checksum | A number that the GameScript calculates from the economic state of the world. |
| clip | The function that limits a value to the range 0 to 1. |
| contract | The delivery objective of a `deliver` task. |
| credit, turn credit | The change in reward that one turn causes. |
| daemon | The background process `railheadd`. It owns the world of one task. |
| dashboard | The short status text that `rh status` shows. |
| dataset | The Hugging Face dataset of tasks and trajectories. |
| delivery distribution | The probability of each delivery quantity, as the agent believes it. |
| deterministic | Gives the same result each time from the same inputs. |
| divergence | A difference between the live record and the replay. |
| dry run | A request for the cost of an action. The engine does not do the action. |
| error code | A name that starts with `ERR_`, for example `ERR_HORIZON`. |
| family, task family | A type of task with its reward formula, for example `deliver` or `simops`. |
| fault | A defect that the generator puts into a transport network. |
| forecast | A meta decision that records a prediction of the agent. |
| fork | A copy of the world in a different engine process. |
| fork-day | One game day that a fork runs. |
| horizon | The number of game days that the agent can play. |
| language model, model | The machine-learning model that an agent uses. |
| ledger, objective ledger | The cargo totals that the GameScript keeps in the savegame. |
| live game, live fork | A game or a fork that has an engine process at this time. |
| lockstep | The rule that the world changes only during a step. |
| machine learning | The procedure that makes a computer model from data. |
| median | The value that has half of a distribution below it. |
| meta decision | A decision that does not change the world: forecast, promise, abstain, flag, or submit. |
| operator | The person or program that starts and stops the environment. |
| oracle | A scripted player that solves a task with player actions only. |
| payout | The value of a promise after the delivery. |
| promise | A meta decision that records the delivery quantity that the agent promises. |
| promise-keeping contract | Mechanism M4: a contract that pays for a correct promise. |
| proof-of-play | Mechanism M1: the reward comes only from a replay of the action log. |
| proper scoring rule | A score that has its best expected value when the agent reports its true belief. |
| query | A request that reads the world and does not change it. |
| reinforcement learning (RL) | Machine learning from rewards. |
| replay | The procedure that sends the action log again to a new engine from the start state. |
| reward | A number from 0 to 1 that the verifier calculates for a task. |
| run-off | The game days after the horizon. The world continues without the agent. |
| run-off valuation | Mechanism M2: the reward uses the state of the company after the run-off. |
| secret | A fact that only the verifier reads. It is in `secret.json`. |
| seed | An integer that sets the random values of a generator. |
| SimOps | The `simops` task family: repair of a transport network. |
| split | A set of seeds: `train`, `dev`, or `ood`. |
| start state | The sealed savegame `start.sav` from which each episode starts. |
| step | A period in which the GameScript lets game time pass. |
| task, task ID | One environment instance and its name, for example `deliver-train-0003`. |
| training | The RL procedure that changes a model with rewards. |
| trajectory | The action log of one episode. |
| turn | A sequence of actions without a wait or a meta decision between them. |
| upper bound | A value that the true value cannot be more than. |
| variant | The generator family in a task ID, for example `deliver_bid`. |
| verifier | The program `railhead.verify` that calculates the reward. |
| wait | A request that lets game time pass. Also, the record of that request in the action log. |
| wake condition | An event that stops a wait before its end. |
| whitelist | The set of GameScript methods that are player actions. |
| world | One live game: an engine process, its admin port link, and its GameScript. |

### OpenTTD and the game

| Technical name | Description |
|---|---|
| admin password | The password of the admin port. Each engine process has a random password. |
| admin port | The TCP interface of an OpenTTD server for administration programs. |
| asset | A vehicle, station, or other property of a company. |
| asset stripping | The sale of company assets to increase cash at the end of the horizon. |
| bankrupt | The state of a company that cannot pay its debts. |
| book value | The value of the assets of a company in its accounts. |
| bridge | A game structure that carries a rail track or a road over an obstacle. |
| cargo | The goods or passengers that vehicles carry in the game. |
| cash | The money of a company in the game. |
| catchment | The area around a station from which the station takes and gives cargo. |
| cheat command | A console command that changes the game without the usual rules. |
| company | One player organization in the game. The agent controls one company. |
| crash | An accident that destroys a vehicle. Also, an unexpected stop of a process. |
| dedicated server | An OpenTTD server without a graphical interface. |
| depot | A game structure where a company buys and repairs vehicles. |
| drag | One straight line of track or road that one build command makes. |
| engine | The patched OpenTTD program. |
| engine ID | The ID of a vehicle model in the game. The `engine` argument of `vehicle.buy` is an engine ID. |
| event | A message that the GameScript sends without a request, for example a vehicle crash. |
| frame | One cycle of the main loop of the engine. |
| game day | 74 ticks of game time. |
| GameScript | A Squirrel script that the engine runs. `RailheadBridge` is the Railhead GameScript. |
| GameScript bridge | The RPC between the Python code and the GameScript. |
| industry | A game structure that makes or accepts cargo. |
| loan | The money that a company must pay back to the bank in the game. |
| monitor, cargo monitor | A counter in the engine for the cargo that a company delivers or picks up. |
| net worth | Cash minus loan. |
| oil wells | A type of industry. Its production can only decrease in the temperate climate. |
| profit | The income of a vehicle minus its costs. |
| RCON | The remote console of the admin port. |
| route | The road, stations, depot, and vehicles that carry one cargo. |
| savegame | An OpenTTD save file (`.sav`). |
| setting | A configuration value of OpenTTD, for example `construction.command_pause_level`. |
| station | A game structure where vehicles load and unload cargo. |
| stop, truck stop | A road station for trucks or buses. |
| subsidy | A payment from the game for a new route between two places. |
| tick | The smallest unit of game time. |
| tile | One square of the game map. Its position is `x,y`. |
| tile index | The integer `y × map width + x`. |
| town | A game settlement with houses and passengers. |
| transfer | An order that unloads cargo at a station for a different vehicle to carry. |
| transport network | All the routes of a company. |
| vehicle | A train, road vehicle, ship, or aircraft in the game. |

### Software and infrastructure

| Technical name | Description |
|---|---|
| argument | A `key=value` input to a command. |
| A* search | A path search that uses a cost estimate to the goal. |
| CLI | Command-line interface. `rh` is the CLI for the agent. `railhead` is the CLI for the operator. |
| codec | The code that writes and reads admin port packets. |
| container | An instance of the image that runs. |
| entry point | The program that the image runs first when a container starts. |
| exit code | The number that a command returns to the shell when it stops. |
| extra | An optional group of Python dependencies, for example `dev` or `hub`. |
| handler | A function in the GameScript that answers one request type. |
| hash | A SHA-256 value of a file. |
| healthcheck | A command that Harbor runs until the environment is ready. |
| image | The Docker image of the Railhead environment. |
| integration test | A test that starts real engine processes. |
| job directory | The directory where Harbor writes the results of a `harbor run` command. |
| loopback address | The network address `127.0.0.1`. Only programs on the same computer can use it. |
| manifest | The file `sealed.sha256`. It lists the hash of each trusted file. |
| marker | A pytest label that selects a group of tests. |
| operating system | The base software of a computer or a container. |
| packet | One message on the admin port. |
| queue | A list of messages that a thread reads in sequence. |
| request, reply | A message to a program and its answer. |
| RPC | Remote procedure call. |
| schema | A JSON description of the arguments of a function. |
| shared mode | The Harbor mode in which the verifier runs in the container of the agent. |
| socket, unix socket | A local communication endpoint for two processes. |
| standard error | The error output stream of a command. |
| tag | A git name for one commit, for example `v0.1.0`. |
| thread | A sequence of instructions that runs at the same time as other threads in a process. |
| token | A secret string that gives access to an account. |
| trial | One run of one task in Harbor. |
| unit test | A test that does not start an engine process. |
| virtual environment | A Python directory with its own packages. |
| workflow | A GitHub Actions file in `.github/workflows/`. |

## Technical verbs

STE lets a writer use technical verbs for computer processes and for processes in a specified field.
These documents use the technical verbs in the table below.

| Technical verb | Description |
|---|---|
| abstain | Record that a contract cannot be completed. |
| accept | Take cargo at an industry, a town, or a station (game). |
| apply | Put a patch into the source code. |
| bind | Connect the GameScript to the company of the agent. |
| build | Make a program from source code. Also, make a structure in the game. |
| call | Start a function or a method. |
| crash | Stop because of an accident or an unexpected error. |
| delete | Remove a file, a directory, or data. |
| deliver | Bring cargo to an industry or a town in the game. |
| demolish | Remove a structure from a tile in the game. |
| download | Copy a file from the internet to a disk. |
| drop | Stop a fork. Also, not use a sample in training. |
| export | Write tasks in the Harbor format. |
| flush | Write the data in a buffer to the file. |
| fork | Make a copy of the world in a different engine process. |
| generate | Make tasks from seeds with a program. |
| install | Put a program on a computer so that it can run. |
| load | Read a savegame into the engine. |
| log in | Connect to a service with a password or a token. |
| mount | Connect a host directory to a container. |
| pause, unpause | Stop or start the game clock. |
| pick up | Take cargo from an industry or a town into a vehicle (game). |
| pin | Set one fixed version of a program. |
| play | Send player actions to the world. |
| promise | Tell the daemon a delivery quantity that the agent will deliver. |
| push | Send an image or a dataset to a registry. |
| raise | Start a Python exception. |
| recover | Make the world the same as before a crash. |
| refuse | Not do a request, and return an error code. |
| reject | Refuse an action log that a player could not make. |
| replay | Send an action log again to a new engine from the start state. |
| reseed | Set a new seed for the random generator of the engine. |
| return | Give a result back to the caller. |
| run | Make a program or a test operate. |
| save | Write the world to a savegame. |
| seal | Record the hashes of the files that the verifier uses. |
| submit | Tell the daemon that the agent has completed its play. |
| suspend | Stop a script for a short time. The engine continues the script later. |
| upload | Copy files to a different computer. |
| wait | Let game time pass for the world of the task. |
| write | Put data into a file. |

## Words that the documents do not use

| Do not use | Use |
|---|---|
| allow | let |
| check (verb) | examine, make sure |
| create | make |
| ensure | make sure |
| get | receive |
| need (verb) | is necessary |
| perform | do |
| prior to | before |
| require | is necessary |
| thus, therefore | because of this |
| verify | make sure |
| -ing nouns, for example "building a road" | a clause, for example "when you build a road" |
