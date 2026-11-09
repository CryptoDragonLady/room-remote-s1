# Room Remote for S1

Room Remote for S1 lets you control Sonos S1 speakers from a local Codex desktop chat. Ask “What’s playing in the kitchen?” to read the current track, or “Pause the kitchen” to control playback. The controller finds rooms by name each time it runs, so you don’t have to look up or save speaker addresses.

It also runs directly from a terminal. Both entry points use the same Python controller and require no third-party Python packages or Sonos API credentials.

## Requirements

- A Sonos system running **S1**. Discovery selects S1 players and omits hidden bonded units such as a Sub.
- A computer on a private IPv4 network that can reach the speakers. Discovery uses multicast UDP port 1900. Control uses HTTP on TCP port 1400.
- Python 3.9 or later. The examples use `python`. If needed, use `py -3` on Windows or `python3` on macOS or Linux.
- For chat control, a local Codex desktop session with plugin support. For terminal control, a checkout of this repository is sufficient.

The controller has been exercised on Windows with a first-generation Play:5 running S1 firmware 11.16.1. Live checks covered discovery, playback, track information, queue contents, volume, and mute status. The automated suite covers multiroom routing and changes to playback and volume using simulated speakers.

## Install in Codex

With a Codex CLI that supports plugin marketplaces, run:

```sh
codex plugin marketplace add CryptoDragonLady/room-remote-s1 --ref main
codex plugin add room-remote-s1@room-remote-local
```

Open a new local desktop chat, select **Room Remote for S1**, and ask **“Find my S1 speakers.”** The reply lists visible rooms and their groups. Use a room name from that list in your next request.

To install a local checkout for development, run `codex plugin marketplace add .` from the repository root in place of the first command. Keep that checkout in place while it is registered as the marketplace source.

## What you can ask

| Request | What it controls |
| --- | --- |
| “Find my S1 speakers.” | Lists visible rooms, speaker models, firmware, group membership, and group coordinators. |
| “What’s playing in the kitchen?” | Reads the group’s state, track, artist, album, playback position, and duration when the source provides them. |
| “Press play in the kitchen.” | Resumes the group’s current source. |
| “Pause the kitchen.” | Pauses the current group. |
| “Skip this track” or “Go back one track in the kitchen.” | Sends one next-track or previous-track command. The source must support skipping. |
| “Show the kitchen queue.” | Returns up to 20 tracks and the total queue length. Ask for the next page to continue. |
| “What’s the kitchen volume?” | Reads the room’s volume and mute state. |
| “Set the kitchen speaker to 25 percent.” | Sets that room’s volume on a 0–100 scale. |
| “Mute the kitchen” or “Unmute the kitchen.” | Changes the named room’s mute state. |

**Grouped rooms share playback.** If Kitchen and Den are grouped, pausing Kitchen pauses both. Setting Kitchen’s volume to 25 changes Kitchen alone. Room Remote reads the group that already exists on the speakers. Use the Sonos controller to change group membership or choose a new music source.

## Terminal usage

Clone the repository and open the controller directory:

```sh
git clone https://github.com/CryptoDragonLady/room-remote-s1.git
cd room-remote-s1/plugins/room-remote-s1/skills/s1-remote/scripts
python remote.py discover
```

Substitute a room from your discovery result for `Kitchen`. Put `--room` **before** the action. A unique visible room can be selected automatically when discovery is complete. For duplicate names, pass the speaker ID returned by `discover`.

```sh
python remote.py --room "Kitchen" status
python remote.py --room "Kitchen" queue --start 20
python remote.py --room "Kitchen" volume
python remote.py --room "Kitchen" set-volume 25
python remote.py --room "Kitchen" pause
python remote.py --room "Kitchen" play
```

The full action list is `discover`, `status`, `queue`, `play`, `pause`, `next`, `previous`, `volume`, `set-volume`, `mute`, and `unmute`. Queue offsets are zero-based. Omit `--start` for the first 20 entries.

Commands return JSON on standard output. Speaker or network errors return JSON on standard error and exit with status 1. Invalid arguments produce usage text and exit with status 2. A playback response can contain both `"accepted": "play"` and `"state": "TRANSITIONING"`. Run `status` again to confirm that the speaker reached `PLAYING`.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Discovery returns an empty room list | Check that the speaker is powered on, running S1, and visible to the S1 Controller app. Connect the computer to the speaker’s LAN. Guest Wi-Fi, VPN routing, wireless client isolation, and firewall rules can interfere with multicast. |
| More than one room matched | Use the exact room name or the ID from a fresh discovery result. |
| The group coordinator is unavailable | Run discovery again. The group may have changed, or its coordinator may be offline. |
| Play or skip is refused | Check the current source. Live streams may reject skips, and a speaker with no playable source may need a selection in the S1 Controller app. After an uncertain skip, check the track before trying again. |
| Music is playing but the queue is empty or shows other tracks | The queue is the group’s stored Sonos queue. A radio or line-in source can play independently of it. |
| Track or artist information is missing | Some sources provide only playback state or elapsed time. Returned metadata comes from the speaker. |

## Network and privacy

The controller discovers speakers on demand, reads their local device descriptions, and routes group commands to the current coordinator. It communicates directly with the speakers over the LAN. It does not run a background service, send telemetry, or store a device inventory or listening history.

When invoked from chat, the requested results are returned to the desktop host and can become part of the conversation. Discovery can reveal room names and speaker identifiers. Playback queries can reveal titles and artists. Remove household details from logs or screenshots before sharing them in an issue.

## Tests, packaging, and contributions

From the **repository root**:

```sh
python -m unittest discover -s tests -v
python tools/package.py
```

The tests run a local HTTP fixture with two grouped S1 rooms and an S2 device. They exercise real SOAP requests, room selection, queue paging, volume limits, skip failures, and the difference between group and individual controls. You can run them without connecting to a Sonos household.

The packaging command builds a versioned ZIP in `dist/` and checks its file list, manifests, Python scripts, and icon. It includes the project license and the icon’s separate license notice.

- `plugins/room-remote-s1/skills/s1-remote/scripts/` contains discovery, speaker control, and the command-line entry point.
- `plugins/room-remote-s1/skills/s1-remote/SKILL.md` defines how desktop chat requests map to commands.
- `tests/test_remote.py` holds the simulated speakers and command tests.
- `tools/package.py` builds the desktop distribution. The local marketplace catalog is in `.agents/plugins/marketplace.json`.

For a new command, include a fixture test that checks which speaker receives the request. For a hardware report, include the model, S1 firmware version, command, and observed result. Redact room names, speaker IDs, addresses, and media titles that belong to your household. [Open a project issue](https://github.com/CryptoDragonLady/room-remote-s1/issues)

## License and commercial use

Celeste Weingartner licenses the plugin under **PolyForm Noncommercial 1.0.0**. It allows personal, noncommercial use, modification, and redistribution under its terms. It also expressly permits use by certain organizations, including schools, charities, and government institutions, regardless of their funding. Include the license terms or their URL and the required copyright notice when sharing copies. [Full license](LICENSE)

For commercial use outside those permissions, contact Celeste Weingartner to negotiate a separate license. Open a licensing inquiry with a brief description of the intended use. Keep confidential business details out of the public issue. [Request a commercial license](https://github.com/CryptoDragonLady/room-remote-s1/issues/new?title=Commercial%20license%20inquiry)

The speaker icon is adapted from the Bootstrap Icons `speaker-fill` glyph and retains its MIT license. [Original icon](https://github.com/twbs/icons/blob/v1.13.1/icons/speaker-fill.svg) · [Icon license](plugins/room-remote-s1/assets/Bootstrap-Icons-LICENSE.txt)
