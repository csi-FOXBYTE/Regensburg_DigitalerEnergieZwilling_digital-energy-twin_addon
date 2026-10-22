<?php
// Matomo's DI config for the instance, loaded after the plugin configs. The
// entrypoint copies it into the volume on every start.

return [
    // Matomo appends "[Query: ?<query string>, CLI mode: 0]" to logged errors.
    // The query string carries e.g. the OIDC code and state of the Keycloak
    // callback, so only the values of module, action and method are kept.
    'log.processors' => Piwik\DI::decorate(function (array $processors) {
        $processors[] = function (array $record) {
            $record['message'] = preg_replace_callback('/(?<=Query: \?).*?(?=, CLI mode: )/s', function ($query) {
                $pairs = explode('&', $query[0]);
                foreach ($pairs as &$pair) {
                    $name = explode('=', $pair, 2)[0];
                    if (!in_array($name, ['module', 'action', 'method'], true)) {
                        $pair = $name . '=removed';
                    }
                }
                return implode('&', $pairs);
            }, $record['message']);
            return $record;
        };
        return $processors;
    }),
];
