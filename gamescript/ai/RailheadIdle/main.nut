class RailheadIdle extends AIController {
	function Start() {
		while (true) { this.Sleep(10000); }
	}
	function Save() { return {}; }
	function Load(version, data) {}
}
