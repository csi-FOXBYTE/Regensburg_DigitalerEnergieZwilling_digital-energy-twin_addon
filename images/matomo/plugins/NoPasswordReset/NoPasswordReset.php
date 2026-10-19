<?php

namespace Piwik\Plugins\NoPasswordReset;

use Exception;
use Piwik\Config;
use Piwik\Plugin;

/**
 * With login via Keycloak, Matomo users get a random password nobody knows.
 * Without this plugin they could set their own via "Lost your password?" (as
 * soon as Matomo can send mail) or via an invitation link, and bypass the
 * Keycloak role check with the password form.
 */
class NoPasswordReset extends Plugin
{
    private const BLOCKED_ACTIONS = ['resetpassword', 'confirmresetpassword', 'acceptinvitation'];

    public function registerEvents()
    {
        return ['Request.dispatch' => 'blockPasswordReset'];
    }

    public function blockPasswordReset(&$module, &$action, &$parameters)
    {
        // PHP method names are case-insensitive, so compare that way too.
        if (
            strcasecmp((string) $module, 'Login') !== 0
            || !in_array(strtolower((string) $action), self::BLOCKED_ACTIONS, true)
        ) {
            return;
        }
        $oidc = Config::getInstance()->LoginOIDC;
        if (is_array($oidc) && !empty($oidc['clientId'])) {
            throw new Exception('Setting a Matomo password is disabled. Please log in via Keycloak.');
        }
    }
}
