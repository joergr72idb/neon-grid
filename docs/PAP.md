# NEON GRID – Programmablaufplan (PAP)

These program flowcharts follow the symbols of **DIN 66001**. Each chart
describes one function or process of the program, and the heading names the
function in the source code. For the big picture, see [ARCHITECTURE.md](ARCHITECTURE.md).

**Legend (DIN 66001)**

```mermaid
flowchart LR
    T(["Start / End<br/>(terminal)"])
    P["Operation<br/>(process)"]
    D{"Decision"}
    IO[/"Input / Output"/]
    S[["Subroutine<br/>(own chart)"]]
    C(("A"))
    C2["Connector:<br/>continue at the<br/>circle with the same letter"]
    C --- C2
```

The charts are written in Mermaid. GitHub, GitLab, VS Code (Markdown preview),
JetBrains IDEs and Obsidian display them as graphics. You can also paste a code
block into https://mermaid.live to view it or export it as PNG/SVG.

**Overview**

| No. | Flowchart | Source |
|---|---|---|
| 1 | Server start | `server.py` → `main`, `make_app` |
| 2 | Registration | `server.py` → `register` |
| 3 | Login | `server.py` → `login` |
| 4 | WebSocket connection of one browser | `server.py` → `websocket` |
| 5 | Matchmaking ("Enter the grid") | `server.py` → `Lobby.join` |
| 6 | Match flow (countdown and game loop) | `server.py` → `Match.run` |
| 7 | One game step (movement and collision) | `server.py` → `Match.step` |
| 8 | Steering command | `server.py` → `Match.turn` |
| 9 | End of a match | `server.py` → `Match.finish`, `Lobby.disconnect` |
| 10 | Browser: page start and menu | `game.js` → `init`, `submitAuth`, `play` |
| 11 | Browser: during a match | `game.js` → `handleMessage`, `onTick`, `onEnd` |
| 12 | Game access point with jump page | `scripts/neongrid-ap.sh`, `server.py` → `captive_portal_middleware` |

---

## 1. Server start (`main`, `make_app`)

```mermaid
flowchart TD
    A(["Start: python server.py …"]) --> B[/"Read command-line options<br/>--host --port --db --static --countdown"/]
    B --> C["Open the SQLite database<br/>(file is created if missing)"]
    C --> D["Create tables if they don't exist yet<br/>users, sessions, matches"]
    D --> E["Create lobby and rate limiters<br/>login: 10 per 10 min, register: 5 per hour"]
    E --> F["Register URL routes<br/>/api/register, /api/login, /api/logout,<br/>/api/me, /api/highscores, /ws"]
    F --> G{"--static given?"}
    G -->|yes| H["Also serve the web page folder<br/>(/ → index.html)"]
    G -->|no| I
    H --> I["Listen on host:port"]
    I --> J[/"Log: Running on http://host:port"/]
    J --> K["Wait for requests<br/>(each one is handled by its own handler)"]
    K --> L{"Ctrl+C / stop signal?"}
    L -->|no| K
    L -->|yes| M(["End"])
```

## 2. Registration (`register`)

```mermaid
flowchart TD
    A(["Start: POST /api/register"]) --> B["Determine client IP<br/>(behind Apache: from X-Forwarded-For)"]
    B --> C{"Fewer than 5 registrations<br/>from this IP in the last hour?"}
    C -->|no| E1[/"Error 429: Too many new accounts"/]
    C -->|yes| D[/"Read username and password from JSON body"/]
    D --> E{"Body valid JSON object?"}
    E -->|no| E2[/"Error 400: Invalid request"/]
    E -->|yes| F{"Username 3–16 characters<br/>A–Z a–z 0–9 _ - ?"}
    F -->|no| E3[/"Error 400: Username rules"/]
    F -->|yes| G{"Password 6–128 characters?"}
    G -->|no| E4[/"Error 400: Password too short"/]
    G -->|yes| H{"Username already taken?<br/>(case-insensitive)"}
    H -->|yes| E5[/"Error 409: Username taken"/]
    H -->|no| I["Create random salt (16 bytes)"]
    I --> J["Compute scrypt hash<br/>(in a worker thread)"]
    J --> K{"Insert user into the database<br/>successful?"}
    K -->|"no (same name registered meanwhile)"| E5
    K -->|yes| L["Count registration for this IP"]
    L --> M["Create session: random token,<br/>store its SHA-256 hash, valid 30 days"]
    M --> N[/"Answer: token + user data"/]
    E1 & E2 & E3 & E4 & E5 & N --> Z(["End"])
```

## 3. Login (`login`)

```mermaid
flowchart TD
    A(["Start: POST /api/login"]) --> B["Determine client IP"]
    B --> C{"Fewer than 10 failed logins<br/>from this IP in the last 10 min?"}
    C -->|no| E1[/"Error 429: Too many failed logins"/]
    C -->|yes| D[/"Read username and password"/]
    D --> E{"Body valid?"}
    E -->|no| E2[/"Error 400: Invalid request"/]
    E -->|yes| F["Look up user by name"]
    F --> G{"User exists?"}
    G -->|yes| H1["Salt = the user's stored salt"]
    G -->|no| H2["Salt = random throwaway salt<br/>(same time as a real check,<br/>so the answer reveals nothing)"]
    H1 & H2 --> I["Compute scrypt hash of the password"]
    I --> J{"User exists AND<br/>hash equals stored hash?<br/>(constant-time compare)"}
    J -->|no| K["Count failed login for this IP"]
    K --> E3[/"Error 401: Wrong username or password"/]
    J -->|yes| L["Create session (token, 30 days)"]
    L --> M[/"Answer: token + user data"/]
    E1 & E2 & E3 & M --> Z(["End"])
```

## 4. WebSocket connection of one browser (`websocket`)

One of these runs for every open browser tab.

```mermaid
flowchart TD
    A(["Start: browser opens /ws"]) --> B["Accept WebSocket<br/>ping every 20 s, max. 4 KB per message"]
    B --> C["conn := none (not logged in)"]
    C --> D[/"Wait for next message"/]
    D --> E{"Connection closed?"}
    E -->|yes| X
    E -->|no| F{"Text message with<br/>valid JSON object?"}
    F -->|no| D
    F -->|yes| G{"Logged in?<br/>(conn exists)"}
    G -->|no| H{"type = auth?"}
    H -->|no| D
    H -->|yes| I{"Token belongs to<br/>a valid session?"}
    I -->|no| J[/"Send auth_failed"/] --> X
    I -->|yes| K["conn := new connection object"]
    K --> L["Lobby.connect:<br/>older connection of the same<br/>account gets 'kicked' and is closed"]
    L --> M[/"Send auth_ok + user data"/] --> D
    G -->|yes| N{"type?"}
    N -->|join| O[["Lobby.join (chart 5)"]] --> D
    N -->|leave| P{"Is this player waiting?"}
    P -->|yes| P1["Remove from waiting slot"] --> P2[/"Send idle"/] --> D
    P -->|no| D
    N -->|turn| Q{"In a match AND<br/>dir is up/down/left/right?"}
    Q -->|yes| R[["Match.turn (chart 8)"]] --> D
    Q -->|no| D
    N -->|other| D
    X{"Was the player logged in?"}
    X -->|yes| Y[["Lobby.disconnect (chart 9)"]] --> Z(["End"])
    X -->|no| Z
```

## 5. Matchmaking (`Lobby.join`)

```mermaid
flowchart TD
    A(["Start: player presses 'Enter the grid'"]) --> B{"Player already in a match<br/>or already waiting?"}
    B -->|yes| Z(["End (ignore double click)"])
    B -->|no| C{"Is someone waiting?"}
    C -->|yes| D{"Waiting connection closed,<br/>or the same account?"}
    D -->|yes| E["Clear waiting slot"] --> F
    D -->|no| G
    C -->|no| F["Put player into the waiting slot"]
    F --> F1[/"Send waiting"/] --> Z
    G["Clear waiting slot<br/>waiting player = player 0 (cyan, left)<br/>new player = player 1 (orange, right)"]
    G --> H["Create match:<br/>empty 120×80 grid, start positions,<br/>start directions right / left"]
    H --> I[["Start Match.run in the background (chart 6)"]]
    I --> Z
```

## 6. Match flow (`Match.run`)

```mermaid
flowchart TD
    A(["Start"]) --> B[/"Send 'match' to both players:<br/>own number, grid size, names,<br/>start positions"/]
    B --> C["n := countdown (default 10)"]
    C --> D[/"Send countdown n to both"/]
    D --> E["Wait 1 second"]
    E --> F{"Match already ended?<br/>(a player left)"}
    F -->|yes| Z(["End"])
    F -->|no| G["n := n − 1"]
    G --> H{"n > 0?"}
    H -->|yes| D
    H -->|no| I[/"Send countdown 0 ('Go!')"/]
    I --> J["next_t := now"]
    J --> K{"Match ended?"}
    K -->|yes| Z
    K -->|no| L[["Match.step (chart 7)<br/>→ heads, crashed"]]
    L --> M[/"Send tick to both:<br/>tick no., new heads, crashed"/]
    M --> N{"At least one<br/>cycle crashed?"}
    N -->|yes| O{"Both crashed?"}
    O -->|yes| P1["winner := none (draw)"]
    O -->|no| P2["winner := the one<br/>who did not crash"]
    P1 & P2 --> Q[["Match.finish(winner, 'crash')<br/>(chart 9)"]] --> Z
    N -->|no| R["speed := min(22, 14 + tick ÷ 120)<br/>ticks per second"]
    R --> S["next_t := next_t + 1 / speed"]
    S --> T["Sleep until next_t"]
    T --> K

    ERR["Any unexpected error"] -.-> ERR2[["Match.finish(none, 'error')"]] -.-> Z
```

## 7. One game step (`Match.step`)

```mermaid
flowchart TD
    A(["Start"]) --> B["i := 0"]
    B --> C{"Turn waiting in<br/>player i's queue?"}
    C -->|yes| D["direction[i] := next turn from queue"]
    C -->|no| E
    D --> E["new head[i] := position[i] + one cell<br/>in direction[i]"]
    E --> F{"i = 0?"}
    F -->|yes| F1["i := 1"] --> C
    F -->|no| G["crashed := [no, no]"]
    G --> H["For each player i"]
    H --> I{"head[i] outside the arena<br/>OR cell already occupied?"}
    I -->|yes| J["crashed[i] := yes"]
    I -->|no| K
    J --> K{"Both players checked?"}
    K -->|no| H
    K -->|yes| L{"head[0] = head[1]?<br/>(head-on collision)"}
    L -->|yes| M["crashed := [yes, yes]"]
    L -->|no| N
    M --> N["For each player that did not crash:<br/>mark cell as occupied,<br/>position := head"]
    N --> O["tick := tick + 1"]
    O --> P(["End: return heads, crashed"])
```

Both cycles are moved and checked **together**: first both new heads, then both
checks, and only then are the cells marked. Neither player gets an advantage
from being processed first.

## 8. Steering command (`Match.turn`)

```mermaid
flowchart TD
    A(["Start: turn(player, direction)"]) --> B{"Match over?"}
    B -->|yes| Z(["End (ignore)"])
    B -->|no| C{"Queue empty?"}
    C -->|yes| D["last := current direction"]
    C -->|no| E["last := last queued turn"]
    D & E --> F{"direction = last<br/>OR direction = opposite of last<br/>OR already 3 turns queued?"}
    F -->|yes| Z
    F -->|no| G["Append direction to the queue"] --> Z
```

## 9. End of a match (`Match.finish`, `Lobby.disconnect`)

```mermaid
flowchart TD
    subgraph disconnect["Lobby.disconnect – browser connection ended"]
        DA(["Start"]) --> DB["Remove from list of active players"]
        DB --> DC{"Was waiting?"}
        DC -->|yes| DD["Clear waiting slot"]
        DC -->|no| DE
        DD --> DE{"In a match?"}
        DE -->|yes| DF["Forfeit: the other player wins"] --> FA
        DE -->|no| DZ(["End"])
    end

    subgraph finish["Match.finish(winner, reason)"]
        FA(["Start"]) --> FB{"Already finished?"}
        FB -->|yes| FZ(["End"])
        FB -->|no| FC["done := yes<br/>(stops countdown / game loop)"]
        FC --> FD["Remove match from the list of running matches"]
        FD --> FE{"reason = error?"}
        FE -->|no| FF["Database, in one transaction:<br/>insert match,<br/>winner wins+1 and loser losses+1,<br/>or both draws+1"]
        FE -->|yes| FG
        FF --> FG[/"Log the result"/]
        FG --> FH["For each player:<br/>no longer in a match"]
        FH --> FI[/"Send end: win / lose / draw,<br/>reason, updated statistics"/]
        FI --> FZ
    end
```

## 10. Browser: page start and menu (`init`, `submitAuth`, `play`)

```mermaid
flowchart TD
    A(["Start: page loaded"]) --> B{"Token saved in the browser?"}
    B -->|no| AUTH
    B -->|yes| C[/"GET api/me"/]
    C --> D{"Answer OK?"}
    D -->|yes| MENU
    D -->|"401 (expired)"| E["Delete saved token"] --> AUTH
    D -->|"other error"| AUTH

    AUTH[/"Show login screen + highscores"/] --> F[/"Player enters name, password<br/>(tab Login or New account)"/]
    F --> G{"New account AND<br/>passwords differ?"}
    G -->|yes| G1[/"Error: passwords do not match"/] --> F
    G -->|no| H[/"POST api/login or api/register"/]
    H --> I{"Successful?"}
    I -->|no| I1[/"Show server's error text"/] --> F
    I -->|yes| J["Save token in the browser"] --> MENU

    MENU[/"Show menu: own statistics + highscores"/] --> K{"Player clicks?"}
    K -->|Log out| L[/"POST api/logout"/] --> L1["Delete token, close WebSocket"] --> AUTH
    K -->|Enter the grid| M["Unlock sound"]
    M --> N{"WebSocket open?"}
    N -->|no| O["Open WebSocket, send auth"]
    O --> P{"Answer?"}
    P -->|auth_failed| P1["Delete token"] --> P2[/"Please log in again"/] --> AUTH
    P -->|connection error| P3[/"Cannot connect to the game server"/] --> MENU
    P -->|auth_ok| Q
    N -->|yes| Q[/"Send join"/]
    Q --> R(("A"))
```

## 11. Browser: during a match (`handleMessage`, `onTick`, `onEnd`)

```mermaid
flowchart TD
    A(("A")) --> B[/"Wait for server message"/]
    B --> C{"type?"}
    C -->|waiting| W[/"Show 'Waiting for an opponent…'"/] --> W1{"Cancel clicked?"}
    W1 -->|yes| W2[/"Send leave"/] --> MENU(["Back to the menu (chart 10)"])
    W1 -->|no| B
    C -->|match| D["Set up game state, show arena,<br/>size the canvases to the window"]
    D --> D1[/"'You are cyan · left side'"/] --> D2["Start animation loop<br/>(pulsing heads, sparks)"] --> B
    C -->|countdown| E[/"Show big number + beep<br/>(0 = 'Go!')"/] --> B
    C -->|tick| F["For each living cycle"]
    F --> G{"crashed?"}
    G -->|yes| H["Mark as dead, explosion + crash sound"]
    G -->|no| I["Add head to trail,<br/>draw the new line segment"]
    H & I --> J{"Both cycles done?"} -->|no| F
    J -->|yes| B
    C -->|"end"| K["Update statistics"]
    K --> L{"result?"}
    L -->|win| L1[/"YOU WIN"/]
    L -->|lose| L2[/"CRASHED"/]
    L -->|draw| L3[/"DRAW"/]
    L1 & L2 & L3 --> M{"Button?"}
    M -->|Play again| R(("A"))
    M -->|Menu| MENU
    C -->|kicked| N["Remember 'kicked'"] --> B

    KEY[/"Input: arrow key / WASD,<br/>swipe of 24 px or more,<br/>or arrow pad touched"/] --> KEY1{"Match running?"}
    KEY1 -->|yes| KEY2[/"Send turn + direction"/]
    KEY1 -->|no| KEY3(["ignore"])

    CLOSE[/"Connection lost"/] --> CL1{"Kicked?"}
    CL1 -->|yes| CL2[/"Menu: 'You opened the game in another window.'"/]
    CL1 -->|no| CL3{"Match running?"}
    CL3 -->|yes| CL4[/"LOST: Connection to the grid was lost"/]
    CL3 -->|no| CL5[/"Menu: Connection lost. Try again."/]
```

"Play again" goes back to the menu and immediately sends `join` again (connector A).

## 12. Game access point with jump page (`scripts/neongrid-ap.sh`)

```mermaid
flowchart TD
    A(["Start: scripts/neongrid-ap.sh"]) --> B{"Running as root?"}
    B -->|no| B1[/"Ask for the sudo password"/] --> B2["Restart the script with sudo"] --> B
    B -->|yes| C{"Password 8–63 chars,<br/>.venv, nmcli and nft present,<br/>port 8765 free?"}
    C -->|no| E1[/"Error message"/] --> Z1(["End, nothing changed"])
    C -->|yes| D["Switch Wi-Fi radio on"]
    D --> D1{"WIFI_DEV set?"}
    D1 -->|yes| D2{"Wi-Fi interface and<br/>managed by NetworkManager?"}
    D2 -->|no| E1
    D2 -->|yes| F[/"Wi-Fi interface: WIFI_DEV<br/>(warning if no AP mode)"/]
    D1 -->|no| E["Take the first Wi-Fi interface that is<br/>managed and lists '* AP' (iw phy … info)"]
    E --> E0{"Found one?"}
    E0 -->|no| E1
    E0 -->|yes| F
    F --> G["Install cleanup for Ctrl+C / exit"]
    G --> G1["Pause system dnsmasq service<br/>if it is running"]
    G1 --> G2{"DNS port 53 free?"}
    G2 -->|no| E3[/"Error: names the program on port 53"/] --> CL
    G2 -->|yes| H["Write DNS setting:<br/>every name → 10.42.0.1,<br/>log lookups"]
    H --> I["Create and start access point:<br/>SSID Neongrid, WPA2, 2.4 GHz,<br/>10.42.0.1/24, DHCP"]
    I --> J["nftables: port 80 → 8765"]
    J --> K["Start game server as normal user<br/>--host 0.0.0.0 --portal http://10.42.0.1/"]
    K --> L{"Server running<br/>after 1 s?"}
    L -->|no| E2[/"Error: server did not start"/] --> CL
    L -->|yes| M[/"Print box: Wi-Fi name, password,<br/>interface, IP 10.42.0.1, game URL<br/>(+ Wi-Fi QR code if qrencode exists)"/]
    M --> N["Wait until the server ends<br/>(Ctrl+C)"]
    N --> CL

    subgraph cleanup["cleanup()"]
        CL["Stop game server"] --> CL1["Delete nftables table"]
        CL1 --> CL2["Switch off and delete access point"]
        CL2 --> CL3["Delete DNS setting file"]
        CL3 --> CL4{"dnsmasq service<br/>paused at start?"}
        CL4 -->|yes| CL5["Start it again"] --> CL6(["End (no Wi-Fi reconnect)"])
        CL4 -->|no| CL6
    end
```

**What happens when a phone joins** (`captive_portal_middleware` in server.py):

```mermaid
flowchart TD
    A(["Phone joins Wi-Fi 'Neongrid'"]) --> B["DHCP: phone gets 10.42.0.x,<br/>DNS server and portal URL"]
    B --> C[/"Phone checks for internet, e.g.<br/>GET http://connectivitycheck.gstatic.com/generate_204"/]
    C --> D["DNS answers 10.42.0.1"]
    D --> E["nftables: port 80 → game server :8765"]
    E --> F{"Host name of the request =<br/>10.42.0.1 or localhost?"}
    F -->|no| G[/"302 redirect to http://10.42.0.1/<br/>(Cache-Control: no-store)"/]
    G --> H["Phone: 'this network has a login page'"]
    H --> I[/"Phone opens http://10.42.0.1/<br/>in its login window"/]
    I --> F
    F -->|yes| J[/"Serve the game page / API / WebSocket"/]
    J --> K(["Player sees NEON GRID"])
```
