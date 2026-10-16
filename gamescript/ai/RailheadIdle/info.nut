/* RailheadIdle — an AI that does nothing. It exists only so the server can create the
 * company the model plays (scripts cannot create companies; RCON `start_ai RailheadIdle` can). */
class RailheadIdleInfo extends AIInfo {
	function GetAuthor()        { return "Railhead"; }
	function GetName()          { return "RailheadIdle"; }
	function GetDescription()   { return "Idle placeholder AI owning the model-controlled company."; }
	function GetVersion()       { return 1; }
	function MinVersionToLoad() { return 1; }
	function GetDate()          { return "2026-10-16"; }
	function CreateInstance()   { return "RailheadIdle"; }
	function GetShortName()     { return "RHID"; }
	function GetAPIVersion()    { return "15"; }
	function UseAsRandomAI()    { return false; }
	function GetSettings()      {}
}
RegisterAI(RailheadIdleInfo());
