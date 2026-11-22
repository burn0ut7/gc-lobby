[BaseContainerProps(), BaseContainerCustomTitleField("m_sFlag")]
class GC_AreaFlagObserver
{
	[Attribute("", UIWidgets.EditBox, "Flag to set on discovery and on changes. Multiple observers can write the same flag in list order.")]
	string m_sFlag;

	[Attribute("", UIWidgets.Object, "An immediate area condition. The observer samples from lobby startup through Match.")]
	ref GC_AreaCondition m_Area;

	[Attribute("0", UIWidgets.CheckBox, "Stop after this area's first change during Match. Discovery and changes during staging do not stop it.")]
	bool m_bStopAfterMatchChange;

	[Attribute("0", UIWidgets.CheckBox, "Only discovery can set the flag. Clear it on ordinary flag changes and after the Match-entry check.")]
	bool m_bResetAfterDiscovery;
}

class GC_AreaFlagObservation
{
	string m_sFlag;
	ref GC_RuleCondition m_Check;
	IEntity m_BoundArea;
	bool m_bBoundToEntity;
	bool m_bStopAfterMatchChange;
	bool m_bResetAfterDiscovery;
	bool m_bDiscovered;
	bool m_bStopped;
	bool m_bLastAnswer;
}
