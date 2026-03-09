<?php declare(strict_types=1);

namespace SiteLockdown\Controller;

use Laminas\Mvc\Controller\AbstractActionController;

class RobotsController extends AbstractActionController
{
    public function indexAction()
    {
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'text/plain; charset=UTF-8');
        $response->setContent("User-agent: *\nDisallow: /\n");
        return $response;
    }
}
