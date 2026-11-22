modded enum ChimeraMenuPreset
{
	GC_OffMapArtilleryMenu,
}

class GC_OffMapArtilleryMenu : ChimeraMenuBase
{
	protected GC_OffMapArtillery m_Artillery;
	protected SCR_ComboBoxComponent m_Shells;
	protected ref array<int> m_aRoundIndices = {};
	protected int m_iLastRemaining = -1;

	//------------------------------------------------------------------------------------------------
	override void OnMenuInit()
	{
		super.OnMenuInit();
		m_Artillery = GC_OffMapArtillery.GetActive();
		PlayerController controller = GetGame().GetPlayerController();
		if (!m_Artillery || !controller)
			return;
		m_Artillery.GetAuthorizedRounds(controller.GetControlledEntity(), m_aRoundIndices);
		m_Shells = SCR_ComboBoxComponent.GetComboBoxComponent("ShellType", GetRootWidget());
		if (!m_Shells || m_aRoundIndices.IsEmpty())
			return;
		m_Shells.ClearAll();
		foreach (int index : m_aRoundIndices)
			m_Shells.AddItem(m_Artillery.GetRound(index).m_roundName);
		m_Shells.SetCurrentItem(0);
		m_Shells.m_OnChanged.Insert(SelectRound);
		SelectRound(m_Shells, 0);
	}

	//------------------------------------------------------------------------------------------------
	protected void SelectRound(SCR_ComboBoxComponent combo, int index)
	{
		if (!m_Artillery || !m_aRoundIndices.IsIndexValid(index))
			return;
		GC_OffMapRound round = m_Artillery.GetRound(m_aRoundIndices[index]);
		string guns = round.m_gunCount.ToString();
		TextWidget.Cast(GetRootWidget().FindAnyWidget("GunCount")).SetText(guns);
		EditBoxWidget.Cast(GetRootWidget().FindAnyWidget("GunSelected")).SetText(guns);
		UpdateRemaining(round.m_roundCount);
	}

	//------------------------------------------------------------------------------------------------
	protected void UpdateRemaining(int remaining)
	{
		if (remaining == m_iLastRemaining)
			return;
		m_iLastRemaining = remaining;
		TextWidget.Cast(GetRootWidget().FindAnyWidget("RoundCount")).SetText(remaining.ToString());
	}

	//------------------------------------------------------------------------------------------------
	override void OnMenuUpdate(float tDelta)
	{
		super.OnMenuUpdate(tDelta);
		if (!m_Artillery || m_Artillery != GC_OffMapArtillery.GetActive() || m_aRoundIndices.IsEmpty())
		{
			ClosePanel();
			return;
		}
		if (m_Shells && m_aRoundIndices.IsIndexValid(m_Shells.GetCurrentIndex()))
			UpdateRemaining(m_Artillery.GetRound(m_aRoundIndices[m_Shells.GetCurrentIndex()]).m_roundCount);
	}

	//------------------------------------------------------------------------------------------------
	void RequestFire()
	{
		GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(GetGame().GetPlayerController());
		if (!controller || !m_Shells || !m_aRoundIndices.IsIndexValid(m_Shells.GetCurrentIndex()))
			return;
		Widget root = GetRootWidget();
		int rounds = EditBoxWidget.Cast(root.FindAnyWidget("RoundSelected")).GetText().ToInt();
		int guns = EditBoxWidget.Cast(root.FindAnyWidget("GunSelected")).GetText().ToInt();
		int dispersion = EditBoxWidget.Cast(root.FindAnyWidget("Dispersion")).GetText().ToInt();
		string grid = EditBoxWidget.Cast(root.FindAnyWidget("GridCords")).GetText();
		controller.GC_RequestOffMapArtillery(m_aRoundIndices[m_Shells.GetCurrentIndex()], rounds, guns, dispersion, grid);
	}

	//------------------------------------------------------------------------------------------------
	void ClosePanel()
	{
		GetGame().GetMenuManager().CloseMenu(this);
	}

	//------------------------------------------------------------------------------------------------
	override void OnMenuClose()
	{
		if (m_Shells)
			m_Shells.m_OnChanged.Remove(SelectRound);
		super.OnMenuClose();
	}
}

class GC_OffMapFireButton : ScriptedWidgetComponent
{
	override bool OnClick(Widget w, int x, int y, int button)
	{
		GC_OffMapArtilleryMenu menu = GC_OffMapArtilleryMenu.Cast(
			GetGame().GetMenuManager().FindMenuByPreset(ChimeraMenuPreset.GC_OffMapArtilleryMenu));
		if (menu)
			menu.RequestFire();
		return true;
	}
}

class GC_OffMapCloseButton : ScriptedWidgetComponent
{
	override bool OnClick(Widget w, int x, int y, int button)
	{
		GC_OffMapArtilleryMenu menu = GC_OffMapArtilleryMenu.Cast(
			GetGame().GetMenuManager().FindMenuByPreset(ChimeraMenuPreset.GC_OffMapArtilleryMenu));
		if (menu)
			menu.ClosePanel();
		return true;
	}
}

[BaseContainerProps()]
class GC_OffMapMenuAvailable : SCR_AvailableActionCondition
{
	override bool IsAvailable(notnull SCR_AvailableActionsConditionData data)
	{
		GC_OffMapArtillery artillery = GC_OffMapArtillery.GetActive();
		GC_LobbyPlayerController controller = GC_LobbyPlayerController.Cast(GetGame().GetPlayerController());
		if (!artillery || !controller)
			return false;
		array<int> indices = {};
		artillery.GetAuthorizedRounds(controller.GetControlledEntity(), indices);
		return !indices.IsEmpty();
	}
}
