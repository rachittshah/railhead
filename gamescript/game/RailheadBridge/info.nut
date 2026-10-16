/* RailheadBridge — lets a model play OpenTTD directly through the Admin Port.
 * Python <-> Admin Port <-> this GameScript, which issues player commands in GSCompanyMode. */
class RailheadBridgeInfo extends GSInfo {
	function GetAuthor()        { return "Railhead"; }
	function GetName()          { return "RailheadBridge"; }
	function GetDescription()   { return "Railhead RL bridge: deterministic lockstep RPC for models playing OpenTTD directly."; }
	function GetVersion()       { return 1; }
	function MinVersionToLoad() { return 1; }
	function GetDate()          { return "2026-10-16"; }
	function CreateInstance()   { return "RailheadBridge"; }
	function GetShortName()     { return "RHBR"; }
	function GetAPIVersion()    { return "15"; }
	function GetURL()           { return "https://github.com/rachittshah/railhead"; }
	function GetSettings()      {}
}
RegisterGS(RailheadBridgeInfo());
