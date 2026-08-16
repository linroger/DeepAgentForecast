import { createRouter, createWebHistory } from 'vue-router'
// The research flow is the product: keep it in the entry chunk.
import ResearchView from '../views/ResearchView.vue'

const routes = [
  {
    path: '/',
    name: 'Research',
    component: ResearchView
  },
  {
    path: '/research',
    redirect: '/'
  },
  {
    // Legacy marketing page. Keeps the route name 'Home' because
    // ResearchView.goHome() navigates via { name: 'Home' }.
    path: '/legacy',
    name: 'Home',
    component: () => import('../views/Home.vue')
  },
  {
    path: '/process/:projectId',
    name: 'Process',
    component: () => import('../views/MainView.vue'),
    props: true
  },
  {
    path: '/simulation/:simulationId',
    name: 'Simulation',
    component: () => import('../views/SimulationView.vue'),
    props: true
  },
  {
    path: '/simulation/:simulationId/start',
    name: 'SimulationRun',
    component: () => import('../views/SimulationRunView.vue'),
    props: true
  },
  {
    path: '/report/:reportId',
    name: 'Report',
    component: () => import('../views/ReportView.vue'),
    props: true
  },
  {
    path: '/interaction/:reportId',
    name: 'Interaction',
    component: () => import('../views/InteractionView.vue'),
    props: true
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/'
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 })
})

export default router
