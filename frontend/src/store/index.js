import { defineStore } from 'pinia'

export const useUserStore = defineStore('user', {
  state: () => ({
    user: null,
    token: localStorage.getItem('access_token') || null
  }),
  getters: {
    isAuthenticated: (state) => !!state.token,
    isAdmin: (state) => state.user?.role === 'admin'
  },
  actions: {
    setToken(token) {
      this.token = token
      localStorage.setItem('access_token', token)
    },
    setUser(user) {
      this.user = user
    },
    logout() {
      this.user = null
      this.token = null
      localStorage.removeItem('access_token')
    }
  }
})
