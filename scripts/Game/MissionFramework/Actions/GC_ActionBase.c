[BaseContainerProps(description: "Shared action base; choose a concrete action instead.", visible: false, insertable: false), BaseContainerCustomStringTitleField("Action Base - Do not use")]
class GC_ActionBase
{
	[Attribute("0", UIWidgets.CheckBox, "Schedule this action independently when the event runs in Match. Scheduled actions are discarded when Match ends.")]
	bool m_bScheduleAction;
	[Attribute("0", UIWidgets.EditBox, "Scheduled delay in seconds. Zero defers to the next rule update.", "0 86400 0.1")]
	float m_fActionDelay;

	//------------------------------------------------------------------------------------------------
	GC_RuleAction BuildAction()
	{
		return null;
	}
}
