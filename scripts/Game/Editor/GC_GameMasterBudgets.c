//! Missions can use the stock editor manager. Its budget cap has no game-mode setting.
modded class SCR_BudgetEditorComponent
{
	//------------------------------------------------------------------------------------------------
	override protected bool IsBudgetCapEnabled()
	{
		if (GC_LobbyGameMode.GetGameMode())
		{
			// The GM budget lives on the manager. Building budgets live on their mode entity.
			SCR_EditorManagerEntity manager = SCR_EditorManagerEntity.Cast(GetOwner());
			if (manager && manager.GetCurrentMode() == EEditorMode.EDIT)
				return false;
		}

		return super.IsBudgetCapEnabled();
	}
}

//! The stock placing component checks batch/occupant costs and clears the selection
//! on budget events independently of the budget component's cap switch.
modded class SCR_PlacingEditorComponent
{
	//------------------------------------------------------------------------------------------------
	protected bool GC_HasUncappedPlacement()
	{
		if (!GC_LobbyGameMode.GetGameMode())
			return false;

		SCR_EditorModeEntity mode = SCR_EditorModeEntity.Cast(GetOwner());
		return mode && mode.GetModeType() == EEditorMode.EDIT;
	}

	//------------------------------------------------------------------------------------------------
	override bool IsThereEnoughBudgetToSpawn(IEntityComponentSource entitySource)
	{
		if (entitySource && GC_HasUncappedPlacement())
			return true;

		return super.IsThereEnoughBudgetToSpawn(entitySource);
	}

	//------------------------------------------------------------------------------------------------
	override protected void OnBudgetMaxReached(EEditableEntityBudget entityBudget, bool maxReached)
	{
		if (GC_HasUncappedPlacement())
			return;

		super.OnBudgetMaxReached(entityBudget, maxReached);
	}
}
