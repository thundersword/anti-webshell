import { defineStore } from 'pinia'

export const useSiteStore = defineStore('site', {
  state: () => ({
    sites: [],
    currentSiteId: localStorage.getItem('current_site_id') || '',
    profiles: []
  }),
  getters: {
    currentSite(state) {
      return state.sites.find((s) => s.site_id === state.currentSiteId) || null
    }
  },
  actions: {
    setSites(sites) {
      this.sites = sites
      if (!this.currentSiteId && sites.length) {
        this.setCurrentSiteId(sites[0].site_id)
      }
      if (this.currentSiteId && !sites.find((s) => s.site_id === this.currentSiteId)) {
        this.setCurrentSiteId(sites[0]?.site_id || '')
      }
    },
    setCurrentSiteId(siteId) {
      this.currentSiteId = siteId || ''
      if (siteId) {
        localStorage.setItem('current_site_id', siteId)
      } else {
        localStorage.removeItem('current_site_id')
      }
    },
    setProfiles(profiles) {
      this.profiles = profiles
    }
  }
})
