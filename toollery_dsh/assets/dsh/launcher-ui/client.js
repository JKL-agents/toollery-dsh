window.__ModuleLoader__.load({
  id: '@local/toollery-launcher-ui',
  factory() {
    return {
      inject: ['workspaces', 'uiWorkspace'],
      apply(ctx) {
        async function openDemo() {
          const response = await fetch('/toollery/launcher', { method: 'POST', credentials: 'same-origin' })
          if (!response.ok) return
          const data = await response.json()
          if (!data.path || !data.launch) return
          const marker = 'toollery-launch:' + data.launch
          if (sessionStorage.getItem(marker)) return
          const workspace = await ctx.workspaces.create({ path: data.path })
          await ctx.uiWorkspace.openWorkspace(workspace.workspaceId, undefined, { agentPreset: data.preset })
          sessionStorage.setItem(marker, 'opened')
        }
        openDemo().catch(() => {
          console.warn('Toollery: automatic demo workspace entry failed. Select a workspace to continue.')
        })
      },
    }
  },
})
