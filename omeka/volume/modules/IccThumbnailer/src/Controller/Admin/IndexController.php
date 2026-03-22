<?php
namespace IccThumbnailer\Controller\Admin;

use IccThumbnailer\Job\RegenerateThumbnails;
use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\ViewModel;

class IndexController extends AbstractActionController
{
    public function indexAction()
    {
        $services = $this->getEvent()->getApplication()->getServiceManager();
        $em = $services->get('Omeka\EntityManager');

        $running = $em->createQuery(
            "SELECT j FROM Omeka\Entity\Job j WHERE j.class = :class AND j.status IN ('starting', 'in_progress') ORDER BY j.id DESC"
        )->setParameter('class', RegenerateThumbnails::class)->setMaxResults(1)->getResult();

        $last = $em->createQuery(
            "SELECT j FROM Omeka\Entity\Job j WHERE j.class = :class AND j.status NOT IN ('starting', 'in_progress') ORDER BY j.id DESC"
        )->setParameter('class', RegenerateThumbnails::class)->setMaxResults(1)->getResult();

        $view = new ViewModel([
            'running' => !empty($running),
            'runningJob' => $running[0] ?? null,
            'lastJob' => $last[0] ?? null,
        ]);
        $view->setTemplate('icc-thumbnailer/admin/index');
        return $view;
    }

    public function regenerateAction()
    {
        if (!$this->getRequest()->isPost()) {
            return $this->redirect()->toRoute('admin/icc-thumbnailer');
        }

        $services = $this->getEvent()->getApplication()->getServiceManager();
        $dispatcher = $services->get('Omeka\Job\Dispatcher');
        $dispatcher->dispatch(RegenerateThumbnails::class);

        $this->messenger()->addSuccess('Thumbnail regeneration job dispatched. Check Jobs for progress.');
        return $this->redirect()->toRoute('admin/icc-thumbnailer');
    }
}
